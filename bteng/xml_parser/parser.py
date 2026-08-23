"""XML tree parser for BTEng.

Supported format::

    <BTEng format_version="1.0">
      <Tree ID="main_tree">
        <Sequence name="root">
          <Condition ID="IsReady"/>
          <Action ID="Move" target="{goal}"/>
          <Node type="CustomNode" param="value"/>
          <SubTree ID="pick" target="{goal}"/>
        </Sequence>
      </Tree>

      <Tree ID="pick">
        <Sequence name="pick_seq">
          <Action ID="Grasp" target="{target}"/>
        </Sequence>
      </Tree>

      <!-- Optional: explicit port declarations -->
      <TreeNodesModel>
        <Action ID="Move">
          <input_port name="target"/>
        </Action>
        <Action ID="Grasp">
          <input_port name="target"/>
        </Action>
      </TreeNodesModel>
    </BTEng>

Port remapping:
  - ``attr="{key}"``  → blackboard lookup / write of *key*
  - ``attr="value"``  → static parameter

Whether ``attr="{key}"`` becomes an *input* or an *output* port is resolved in
this order:

  1. a ``<TreeNodesModel>`` block in the document being parsed
     (``input_port`` / ``output_port`` / ``inout_port``),
  2. a port model seeded on the parser instance before parsing
     (``parser._port_model``),
  3. the registered node's own ``provided_ports()`` declaration, via the
     factory manifest,
  4. input (the historical default for an undeclared attribute).

An ``inout``/``inout_port`` port is registered as *both* an input and an output.

Extensibility:
  - Any tag not recognised as a built-in control/decorator is looked up in
    the NodeFactory registry.  This means new node types need zero parser changes.
  - ``<Node type="Foo" .../>`` is an explicit generic form.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import NodeConfig, TreeNode
from bteng.factory.factory import NodeFactory

_LOG = logging.getLogger(__name__)

# Built-in tag → factory key mapping
_CONTROL_TAGS = {
    "Sequence", "Fallback", "Selector",
    "Parallel", "ReactiveSequence", "ReactiveFallback",
}
_DECORATOR_TAGS = {
    "Inverter", "Retry", "Timeout", "RateController",
    "ForceSuccess", "ForceFailure",
}
_SUBTREE_TAG = "SubTree"
_GENERIC_TAG = "Node"

# Attributes that are XML meta (not node ports/params)
_META_ATTRS = {"name", "ID", "type"}

_BB_REF_RE = re.compile(r"^\{(\w+)\}$")
# Anything shaped like a blackboard reference, including malformed ones such as
# "{a b}" — used only to produce a helpful error on attributes that must be
# literal.
_BRACE_FORM_RE = re.compile(r"^\s*\{.*\}\s*$", re.DOTALL)

# Port direction tags accepted inside a <TreeNodesModel> node entry.  The bare
# spellings are accepted too, for port models seeded programmatically.
_INPUT_PORT_TAGS = {"input_port", "inout_port", "input", "inout"}
_OUTPUT_PORT_TAGS = {"output_port", "inout_port", "output", "inout"}


def _parse_attr(value: str) -> Tuple[str, bool]:
    """Returns (key_or_value, is_blackboard_ref)."""
    m = _BB_REF_RE.match(value)
    if m:
        return m.group(1), True
    return value, False


def _is_bb_ref(raw: str) -> bool:
    """True for a *well-formed* ``{key}``, which binds as a port.

    Deliberately the strict form: ``{a b}`` is not a key, so it falls through to
    the numeric conversion and is reported as "must be a literal number" rather
    than being bound as a port that could never resolve.
    """
    return bool(_BB_REF_RE.match(raw))


def _reject_bb_ref(tag: str, attr: str, raw: str) -> None:
    """Structural constructor arguments cannot be blackboard references.

    ``success_threshold``, ``max_attempts``, ``msec``, ``duration`` and ``hz``
    are passed to the node constructor while the tree is being *built*, so there
    is nothing to resolve ``{key}`` against yet.  Say so instead of feeding
    "{key}" to int()/float().
    """
    if _BRACE_FORM_RE.match(raw):
        raise ValueError(
            f"<{tag} {attr}={raw!r}>: must be a literal number — this attribute "
            f"is a constructor argument resolved when the tree is built, so a "
            f"blackboard reference cannot be used here."
        )


def _int_attr(tag: str, attr: str, raw: str) -> int:
    """int(raw) with an error message that names the element and attribute."""
    _reject_bb_ref(tag, attr, raw)
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        raise ValueError(f"<{tag} {attr}={raw!r}>: expected an integer") from None


def _float_attr(tag: str, attr: str, raw: str) -> float:
    """float(raw) with an error message that names the element and attribute."""
    _reject_bb_ref(tag, attr, raw)
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        raise ValueError(f"<{tag} {attr}={raw!r}>: expected a number") from None


class XMLTreeParser:
    """Parse a BTEng XML file into a live TreeNode hierarchy.

    A parser instance is reusable: every ``parse_*`` call starts from a clean
    document state, so trees and port declarations from an earlier document can
    never leak into a later one.
    """

    def __init__(self, factory: Optional[NodeFactory] = None) -> None:
        self._factory = factory or NodeFactory.get_instance()
        # Caller-seeded port directions: node_id → {port: direction}.  Survives
        # across documents on purpose — it is configuration, not document state.
        self._port_model: Dict[str, Dict[str, str]] = {}
        # Port directions from the <TreeNodesModel> of the document being
        # parsed.  Reset per document; takes precedence over _port_model.
        self._doc_port_model: Dict[str, Dict[str, str]] = {}
        self._tree_roots: Dict[str, ET.Element] = {}
        self._building_stack: List[str] = []  # cycle detection for subtree references
        # (tree_id, exception) for trees that failed to build during the last
        # parse_*_to_registry() call.  Empty when everything built.
        self.registry_errors: List[Tuple[str, Exception]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(
        self,
        path: str,
        tree_id: Optional[str] = None,
        blackboard: Optional[Blackboard] = None,
    ) -> TreeNode:
        tree = ET.parse(path)
        return self._parse_root(tree.getroot(), tree_id=tree_id, blackboard=blackboard)

    def parse_string(
        self,
        xml_str: str,
        tree_id: Optional[str] = None,
        blackboard: Optional[Blackboard] = None,
    ) -> TreeNode:
        root_el = ET.fromstring(xml_str)
        return self._parse_root(root_el, tree_id=tree_id, blackboard=blackboard)

    def parse_file_to_registry(
        self,
        path: str,
        blackboard: Optional[Blackboard] = None,
    ) -> "Any":
        """Parse all trees from a file and return a populated TreeRegistry.

        Each ``<Tree ID="...">`` becomes a standalone Tree in the registry, with
        its own child scope of the supplied blackboard (or of a fresh one if
        omitted) named after the tree ID — so trees see the supplied
        blackboard's keys but their own writes stay in their own scope.

        A tree that fails to build does not abort the whole registry: it is
        logged as a warning and recorded in :attr:`registry_errors` as a
        ``(tree_id, exception)`` pair, and the remaining trees are registered.
        Check ``parser.registry_errors`` (or the log) rather than discovering
        the failure later as a ``None`` from ``registry.get(tree_id)``.
        """
        tree = ET.parse(path)
        return self._parse_all_to_registry(tree.getroot(), blackboard)

    def parse_string_to_registry(
        self,
        xml_str: str,
        blackboard: Optional[Blackboard] = None,
    ) -> "Any":
        """Parse all trees from an XML string and return a populated TreeRegistry.

        See :meth:`parse_file_to_registry` for blackboard scoping and for how
        per-tree failures are reported.
        """
        root_el = ET.fromstring(xml_str)
        return self._parse_all_to_registry(root_el, blackboard)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_document_state(self) -> None:
        """Drop everything learned from a previously parsed document.

        Without this, reusing one parser for a second document returned the
        first document's tree and let a ``<SubTree ID="...">`` resolve against a
        tree from an unrelated file.  The caller-seeded ``_port_model`` is
        configuration rather than document state and is deliberately kept.
        """
        self._tree_roots.clear()
        self._doc_port_model.clear()
        self._building_stack.clear()
        self.registry_errors = []

    def _load_document(self, root_el: ET.Element) -> None:
        """Reset per-document state, then index <TreeNodesModel> and <Tree>."""
        self._reset_document_state()

        model_el = root_el.find("TreeNodesModel")
        if model_el is not None:
            self._load_port_model(model_el)

        for tree_el in root_el.findall("Tree"):
            tid = tree_el.get("ID")
            if not tid:
                continue
            if tid in self._tree_roots:
                raise ValueError(
                    f"Duplicate <Tree ID='{tid}'> in document: a tree ID must be "
                    f"unique. Rename one of the two definitions."
                )
            self._tree_roots[tid] = tree_el

    def _parse_all_to_registry(
        self,
        root_el: ET.Element,
        blackboard: Optional[Blackboard],
    ) -> "Any":
        from bteng.core.tree import Tree, TreeMetadata, TreeRegistry

        bb = blackboard or Blackboard.create()
        self._load_document(root_el)

        registry = TreeRegistry()
        failures: List[Tuple[str, Exception]] = []
        for tree_id, tree_el in self._tree_roots.items():
            try:
                children = [c for c in tree_el if not self._is_whitespace(c)]
                if len(children) != 1:
                    raise ValueError(
                        f"Tree '{tree_id}' must have exactly one root child node, "
                        f"found {len(children)}"
                    )
                child_bb = bb.create_child_scope(tree_id)
                root_node = self._build_node(children[0], child_bb)
                tree = Tree(TreeMetadata(id=tree_id), root_node, child_bb)
                registry.register_tree(tree)
            except Exception as exc:  # one bad tree must not kill the registry
                failures.append((tree_id, exc))
                _LOG.warning(
                    "Tree '%s' failed to build and was not registered: %s: %s",
                    tree_id, type(exc).__name__, exc,
                )

        self.registry_errors = failures
        return registry

    def _parse_root(
        self,
        root_el: ET.Element,
        tree_id: Optional[str],
        blackboard: Optional[Blackboard],
    ) -> TreeNode:
        bb = blackboard or Blackboard.create()

        # Reset per-document state, then index <TreeNodesModel> and <Tree>
        self._load_document(root_el)

        # Determine which tree to run
        if tree_id:
            target_id = tree_id
        else:
            main_attr = root_el.get("main_tree_to_execute")
            if main_attr:
                target_id = main_attr
            elif self._tree_roots:
                target_id = next(iter(self._tree_roots))
            else:
                raise ValueError("No <Tree> found in XML")

        if target_id not in self._tree_roots:
            raise KeyError(f"Tree ID '{target_id}' not found in XML")

        tree_el = self._tree_roots[target_id]
        children = [c for c in tree_el if not self._is_whitespace(c)]
        if len(children) != 1:
            raise ValueError(f"Tree '{target_id}' must have exactly one root child node")

        return self._build_node(children[0], bb)

    def _load_port_model(self, model_el: ET.Element) -> None:
        for node_el in model_el:
            node_id = node_el.get("ID", "")
            ports: Dict[str, str] = {}
            for port_el in node_el:
                name = port_el.get("name", "")
                # "input_port", "output_port" or "inout_port"
                direction = str(port_el.tag)
                ports[name] = direction
            self._doc_port_model[node_id] = ports

    def _port_directions(self, node_type: str) -> Dict[str, str]:
        """Declared directions for *node_type*: document model over seeded model."""
        seeded = self._port_model.get(node_type)
        declared = self._doc_port_model.get(node_type)
        if not seeded:
            return declared or {}
        if not declared:
            return seeded
        return {**seeded, **declared}

    def _build_node(self, el: ET.Element, bb: Blackboard) -> TreeNode:
        tag = el.tag
        node_name = el.get("name", tag)
        node_id = el.get("ID", tag)

        # ------ SubTree ------------------------------------------------
        if tag == _SUBTREE_TAG:
            return self._build_subtree(el, bb)

        # ------ Generic <Node type="..."> ------------------------------
        if tag == _GENERIC_TAG:
            node_type = el.get("type")
            if not node_type:
                raise ValueError("<Node> element missing 'type' attribute")
            return self._build_generic(node_type, node_name, el, bb)

        # ------ Control nodes -----------------------------------------
        if tag in _CONTROL_TAGS:
            children = [self._build_node(c, bb) for c in el if not self._is_whitespace(c)]
            config = self._make_config(node_id, el, bb, output_ports={})
            kwargs = self._control_kwargs(tag, el)
            return self._factory.create_control(tag, node_name, children, config, **kwargs)

        # ------ Decorator nodes ----------------------------------------
        if tag in _DECORATOR_TAGS:
            child_els = [c for c in el if not self._is_whitespace(c)]
            if len(child_els) != 1:
                raise ValueError(f"Decorator '{tag}' must have exactly one child")
            child = self._build_node(child_els[0], bb)
            config = self._make_config(node_id, el, bb)
            kwargs = self._decorator_kwargs(tag, el)
            return self._factory.create_decorator(tag, node_name, child, config, **kwargs)

        # ------ Leaf nodes (Action, Condition, or custom registered) ---
        if self._factory.is_registered(node_id):
            return self._build_generic(node_id, node_name, el, bb)

        # ------ Unknown — try factory with tag name --------------------
        if self._factory.is_registered(tag):
            return self._build_generic(tag, node_name, el, bb)

        raise ValueError(
            f"Unknown node tag <{tag} ID={node_id!r}>. "
            "Register the node type via NodeFactory or use <Node type='...'/>."
        )

    def _build_subtree(self, el: ET.Element, parent_bb: Blackboard) -> TreeNode:
        subtree_id = el.get("ID")
        if not subtree_id:
            raise ValueError("<SubTree> missing 'ID' attribute")
        if subtree_id not in self._tree_roots:
            raise KeyError(f"SubTree ID '{subtree_id}' not defined")
        if subtree_id in self._building_stack:
            cycle = " → ".join(self._building_stack) + f" → {subtree_id}"
            raise ValueError(f"Cyclic subtree reference detected: {cycle}")

        # Build port remapping: attr="{key}" → remap local attr → parent key
        remapping: Dict[str, str] = {}
        static_vals: Dict[str, Any] = {}
        for attr, val in el.attrib.items():
            if attr in _META_ATTRS:
                continue
            bb_key, is_ref = _parse_attr(val)
            if is_ref:
                remapping[attr] = bb_key
            else:
                static_vals[attr] = val

        child_bb = Blackboard.create_child(parent_bb, remapping)
        for k, v in static_vals.items():
            child_bb.set(k, v)

        tree_el = self._tree_roots[subtree_id]
        children = [c for c in tree_el if not self._is_whitespace(c)]
        if len(children) != 1:
            raise ValueError(f"SubTree '{subtree_id}' must have exactly one root child")

        self._building_stack.append(subtree_id)
        try:
            subtree_root = self._build_node(children[0], child_bb)
        finally:
            self._building_stack.pop()

        name = el.get("name", subtree_id)
        from bteng.nodes.subtree import SubTree

        return SubTree(name=name, child=subtree_root)

    def _build_generic(
        self, node_type: str, name: str, el: ET.Element, bb: Blackboard
    ) -> TreeNode:
        port_meta = self._port_directions(node_type)
        input_ports: Dict[str, str] = {}
        output_ports: Dict[str, str] = {}
        params: Dict[str, Any] = {}

        # Seed params from declared InputPort defaults so XML attributes override
        manifest = self._factory.manifest(node_type)
        declared: Dict[str, Any] = {}
        if manifest:
            for port in manifest.ports:
                declared[port.name] = port
                if port.is_input() and port.default is not None:
                    params[port.name] = port.default

        for attr, val in el.attrib.items():
            if attr in _META_ATTRS:
                continue
            bb_key, is_ref = _parse_attr(val)
            if is_ref:
                is_input, is_output = self._resolve_direction(attr, port_meta, declared)
                if is_input:
                    input_ports[attr] = bb_key
                if is_output:
                    output_ports[attr] = bb_key
            else:
                params[attr] = val

        config = NodeConfig(
            blackboard=bb,
            input_ports=input_ports,
            output_ports=output_ports,
            params=params,
        )
        return self._factory.create_leaf(node_type, name, config)

    @staticmethod
    def _resolve_direction(
        attr: str,
        port_meta: Dict[str, str],
        declared: Dict[str, Any],
    ) -> Tuple[bool, bool]:
        """Decide whether ``attr="{key}"`` is an input, an output, or both.

        An explicit ``<TreeNodesModel>`` (or a caller-seeded port model) wins.
        Otherwise the node's own ``provided_ports()`` declaration decides, via
        the factory manifest — without this, ``OutputPort("result")`` written as
        ``result="{reading}"`` was filed as an input and every ``set_output()``
        silently returned False.  An undeclared attribute stays an input, which
        is what the parser has always assumed.
        """
        direction = port_meta.get(attr)
        if direction is not None:
            is_output = direction in _OUTPUT_PORT_TAGS
            # An unrecognised direction tag keeps the historical default (input)
            # rather than silently dropping the port from both maps.
            is_input = direction in _INPUT_PORT_TAGS or not is_output
            return is_input, is_output

        port = declared.get(attr)
        if port is not None:
            return bool(port.is_input()), bool(port.is_output())

        return True, False

    def _make_config(
        self,
        node_id: str,
        el: ET.Element,
        bb: Blackboard,
        output_ports: Optional[Dict[str, str]] = None,
    ) -> NodeConfig:
        params: Dict[str, Any] = {}
        input_ports: Dict[str, str] = {}
        for attr, val in el.attrib.items():
            if attr in _META_ATTRS:
                continue
            key, is_ref = _parse_attr(val)
            if is_ref:
                # Control and decorator nodes declare ports too (Retry's
                # num_attempts, Timeout's duration, RateController's hz,
                # Parallel's success_threshold), and they read them every tick.
                # Binding the ref here is what lets a retry count or a timeout
                # come from the blackboard instead of being frozen at build time.
                input_ports[attr] = key
            else:
                params[attr] = val
        return NodeConfig(
            blackboard=bb,
            input_ports=input_ports,
            params=params,
            output_ports=output_ports or {},
        )

    @staticmethod
    def _control_kwargs(tag: str, el: ET.Element) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if tag == "Parallel":
            st = el.get("success_threshold")
            ft = el.get("failure_threshold")
            if st is not None and not _is_bb_ref(st):
                kwargs["success_threshold"] = _int_attr(tag, "success_threshold", st)
            if ft is not None and not _is_bb_ref(ft):
                kwargs["failure_threshold"] = _int_attr(tag, "failure_threshold", ft)
        return kwargs

    @staticmethod
    def _decorator_kwargs(tag: str, el: ET.Element) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if tag == "Retry":
            for attr in ("max_attempts", "num_attempts"):
                raw = el.get(attr)
                if raw is not None:
                    if _is_bb_ref(raw):
                        break  # bound as a port instead; read each tick
                    kwargs["max_attempts"] = _int_attr(tag, attr, raw)
                    break
        elif tag == "Timeout":
            msec = el.get("msec")
            duration = el.get("duration")
            if msec is not None and _is_bb_ref(msec):
                # Timeout declares a `duration` port, not `msec`: binding msec
                # would look right and do nothing. Point at the form that works.
                raise ValueError(
                    f"<{tag} msec={msec!r}>: msec must be a literal number — it is "
                    f"converted to seconds while the tree is built. Use "
                    f'duration="{msec}" (seconds) to read it from the blackboard.'
                )
            if msec is not None and not _is_bb_ref(msec):
                kwargs["duration"] = _float_attr(tag, "msec", msec) / 1000.0
            elif duration is not None and not _is_bb_ref(duration):
                kwargs["duration"] = _float_attr(tag, "duration", duration)
        elif tag == "RateController":
            raw = el.get("hz")
            if raw is not None and not _is_bb_ref(raw):
                hz = _float_attr(tag, "hz", raw)
                # RateController computes 1.0 / hz in its constructor: a
                # non-positive rate is a ZeroDivisionError or a negative period
                # several layers away from the XML that caused it.
                if not hz > 0:
                    raise ValueError(
                        f"<{tag} hz={raw!r}>: expected a positive number of "
                        f"ticks per second"
                    )
                kwargs["hz"] = hz
        return kwargs

    @staticmethod
    def _is_whitespace(el: ET.Element) -> bool:
        return el.tag is ET.Comment  # type: ignore[comparison-overlap]
