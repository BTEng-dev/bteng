"""Node registry and factory for BTEng."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
from xml.sax.saxutils import quoteattr

from bteng.core.node import NodeConfig, NodeStatus, NodeType, PortDefinition, TreeNode

logger = logging.getLogger(__name__)

#: Every registration performed by the @register_node decorator, in decoration
#: order.  Module-level decorators only run once per interpreter, so the list is
#: replayed whenever a fresh singleton is built (see NodeFactory.get_instance)
#: — otherwise NodeFactory.reset_instance() would drop them permanently.
_DECORATOR_REGISTRATIONS: List[Tuple[Type[TreeNode], Optional[str]]] = []


class PluginLoadError(ImportError):
    """A plugin file could not be imported.

    Names the offending file; the original exception is kept as ``__cause__``.
    Subclasses ImportError so existing ``except ImportError`` handlers around
    ``load_plugin()`` keep working.
    """


def _attr(value: Any) -> str:
    """Quote *value* for use as an XML attribute (escapes & < > and quotes)."""
    return quoteattr("" if value is None else str(value))


# ── NodeManifest ──────────────────────────────────────────────────────────────

@dataclass
class NodeManifest:
    """Static description of a node type — used for tooling and documentation.

    Does not require an instance of the node.  All registered manifests can
    be exported via NodeFactory.export_node_models_xml().
    """
    type_name:   str              = ""
    node_type:   NodeType         = NodeType.ACTION
    ports:       List[PortDefinition] = field(default_factory=list)
    description: str              = ""
    version:     str              = "1.0"


# ── NodeFactory ───────────────────────────────────────────────────────────────

class NodeFactory:
    """Singleton registry that creates TreeNode instances by name.

    Built-in nodes are pre-registered in __init__.
    User nodes register via register() or the @register_node decorator.

    Usage::

        factory = NodeFactory.get_instance()
        factory.register(MyAction)                # uses class name
        factory.register(MyAction, "my_alias")   # custom name

        node = factory.create_leaf("MyAction", "instance_name", config)
    """

    _instance: Optional["NodeFactory"] = None

    def __init__(self) -> None:
        self._registry: Dict[str, Type[TreeNode]] = {}
        self._manifests: Dict[str, NodeManifest]  = {}
        self._register_builtins()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "NodeFactory":
        if cls._instance is None:
            instance = cls()
            # Assign before replaying: register_node() calls get_instance().
            cls._instance = instance
            instance._replay_decorator_registrations()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (useful in tests).

        The next get_instance() rebuilds the built-ins *and* replays every
        @register_node registration, so decorator-registered nodes survive a
        reset even though their module-level decorators never run again.
        """
        cls._instance = None

    def _replay_decorator_registrations(self) -> None:
        """Re-apply every @register_node registration to this instance.

        De-duplicated by registry key, keeping the last registration for each
        key — the resulting state matches a fresh interpreter, and replay never
        emits spurious "overwriting" warnings.
        """
        latest: Dict[str, Type[TreeNode]] = {}
        for node_class, name in _DECORATOR_REGISTRATIONS:
            latest[name or node_class.__name__] = node_class
        for key, node_class in latest.items():
            self.register(node_class, key)

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        node_class: Type[TreeNode],
        name: Optional[str] = None,
        manifest: Optional[NodeManifest] = None,
    ) -> None:
        key = name or node_class.__name__
        existing = self._registry.get(key)
        if existing is not None and existing is not node_class:
            # Re-registering the *same* class stays silent: register_nodes()
            # helpers in downstream packages are documented as idempotent and
            # call this repeatedly.  A *different* class is a real shadowing
            # (e.g. a plugin taking over the built-in "Sequence") — say so.
            logger.warning(
                "NodeFactory: node type %r is being overwritten: %s.%s replaces %s.%s",
                key,
                getattr(node_class, "__module__", "?"), node_class.__name__,
                getattr(existing, "__module__", "?"), existing.__name__,
            )
        self._registry[key] = node_class

        # Build manifest from class if not provided explicitly
        if manifest is None:
            ports: List[PortDefinition] = []
            if hasattr(node_class, "provided_ports"):
                try:
                    ports = node_class.provided_ports()
                except Exception:
                    pass
            manifest = NodeManifest(
                type_name=key,
                node_type=getattr(node_class, "node_type", NodeType.ACTION),
                ports=ports,
                description=getattr(node_class, "__doc__", "") or "",
            )
        self._manifests[key] = manifest

    def register_many(self, mapping: Dict[str, Type[TreeNode]]) -> None:
        for name, cls in mapping.items():
            self.register(cls, name)

    def is_registered(self, name: str) -> bool:
        return name in self._registry

    def registered_names(self) -> List[str]:
        return list(self._registry.keys())

    # ── Manifest access ───────────────────────────────────────────────────────

    def manifest(self, name: str) -> Optional[NodeManifest]:
        """Returns None if type_name is not registered."""
        return self._manifests.get(name)

    def all_manifests(self) -> List[NodeManifest]:
        return list(self._manifests.values())

    def export_node_models_xml(self) -> str:
        """Serialise all manifests as a <TreeNodesModel> XML block.

        Compatible with the BTEng XML format and Groot2/BT.CPP tooling.

        Every interpolated attribute value is XML-quoted, so free-text port
        descriptions containing ``"``, ``<`` or ``&`` still produce a document
        that XMLTreeParser (and any other consumer) can parse.
        """
        lines = ["<TreeNodesModel>"]
        for m in self._manifests.values():
            tag = {
                NodeType.ACTION:    "Action",
                NodeType.CONDITION: "Condition",
                NodeType.CONTROL:   "Control",
                NodeType.DECORATOR: "Decorator",
            }.get(m.node_type, "Action")
            lines.append(f'  <{tag} ID={_attr(m.type_name)}>')
            for port in m.ports:
                dir_tag = {
                    "input":  "input_port",
                    "output": "output_port",
                    "inout":  "inout_port",
                }.get(port.direction.value, "input_port")
                lines.append(
                    f'    <{dir_tag} name={_attr(port.name)} '
                    f'description={_attr(port.description)}/>'
                )
            lines.append(f"  </{tag}>")
        lines.append("</TreeNodesModel>")
        return "\n".join(lines)

    # ── Creation ──────────────────────────────────────────────────────────────

    def create_leaf(self, node_type: str, name: str, config: NodeConfig) -> TreeNode:
        """Create a leaf node (Action / Condition). No children."""
        cls = self._lookup(node_type)
        return cls(name=name, config=config)

    def create_control(
        self, node_type: str, name: str, children: list, config: NodeConfig, **kwargs: Any
    ) -> TreeNode:
        cls = self._lookup(node_type)
        return cls(name=name, children=children, config=config, **kwargs)

    def create_decorator(
        self, node_type: str, name: str, child: TreeNode, config: NodeConfig, **kwargs: Any
    ) -> TreeNode:
        cls = self._lookup(node_type)
        return cls(name=name, child=child, config=config, **kwargs)

    def create(self, node_type: str, name: str, config: NodeConfig, **kwargs: Any) -> TreeNode:
        """Generic create — infers constructor signature from class hierarchy."""
        from bteng.core.node import ControlNode, DecoratorNode

        cls = self._lookup(node_type)
        children = kwargs.pop("children", None)
        child    = kwargs.pop("child", None)

        if children is not None:
            return cls(name=name, children=children, config=config, **kwargs)
        if child is not None:
            return cls(name=name, child=child, config=config, **kwargs)
        return cls(name=name, config=config, **kwargs)

    # ── Plugin loading ────────────────────────────────────────────────────────

    def load_plugin(self, module_path: str) -> None:
        """Load a Python module file and register all exported TreeNode subclasses.

        If the module exports a ``BTENG_NODES`` list of ``(name, class)`` pairs,
        only those nodes are registered.  Otherwise all TreeNode subclasses
        *defined in that module* (not re-imported from elsewhere) are registered.

        The plugin is imported under a path-unique module name and inserted into
        ``sys.modules`` under exactly that name *before* its body runs, so the
        classes it defines are reachable via ``sys.modules[cls.__module__]``.
        Anything that resolves string annotations through ``sys.modules`` —
        ``dataclasses``, ``typing.get_type_hints``, ``pickle`` — therefore works
        in a plugin using ``from __future__ import annotations``.

        Raises PluginLoadError (an ImportError) if the plugin body raises; the
        original exception is preserved as ``__cause__``.
        """
        # Path-unique key so two plugins never share a module name (their
        # classes would otherwise report the same __module__ and one module
        # object would shadow the other in sys.modules).
        mod_key = "_bteng_plugin_" + hashlib.md5(module_path.encode()).hexdigest()[:12]

        spec = importlib.util.spec_from_file_location(mod_key, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin: {module_path}")
        mod = importlib.util.module_from_spec(spec)

        previous = sys.modules.get(mod_key)
        sys.modules[mod_key] = mod
        loaded = False
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            loaded = True
        except Exception as exc:
            raise PluginLoadError(
                f"Failed to load plugin '{module_path}': {exc.__class__.__name__}: {exc}"
            ) from exc
        finally:
            if not loaded:
                # Never leave a half-initialised module behind.
                if previous is not None:
                    sys.modules[mod_key] = previous
                else:
                    sys.modules.pop(mod_key, None)

        if hasattr(mod, "BTENG_NODES"):
            for name, cls in mod.BTENG_NODES:
                self.register(cls, name)
        else:
            self._auto_discover(mod)

    def load_module(self, module_name: str) -> None:
        """Load a Python importable module (e.g., 'my_package.nodes')."""
        mod = importlib.import_module(module_name)
        if hasattr(mod, "BTENG_NODES"):
            for name, cls in mod.BTENG_NODES:
                self.register(cls, name)
        else:
            self._auto_discover(mod)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _lookup(self, name: str) -> Type[TreeNode]:
        if name not in self._registry:
            raise KeyError(
                f"Unknown node type '{name}'. "
                f"Registered: {sorted(self._registry)}"
            )
        return self._registry[name]

    def _auto_discover(self, mod: Any) -> None:
        """Register TreeNode subclasses defined in mod (not merely imported there).

        Filters out:
        - Classes whose __module__ != mod.__name__ (re-imported base classes).
        - Classes with abstract methods (have non-empty __abstractmethods__).
        - Private names (starting with '_').
        """
        mod_name = getattr(mod, "__name__", None)
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            obj = getattr(mod, attr_name)
            try:
                if not (isinstance(obj, type) and issubclass(obj, TreeNode)):
                    continue
                if obj is TreeNode:
                    continue
                # Only register classes DEFINED in this module to avoid
                # accidentally registering re-imported base classes.
                if mod_name and getattr(obj, "__module__", None) != mod_name:
                    continue
                # Skip truly abstract classes.
                if getattr(obj, "__abstractmethods__", None):
                    continue
                self.register(obj)
            except TypeError:
                pass

    def _register_builtins(self) -> None:
        from bteng.nodes.control.sequence import SequenceNode
        from bteng.nodes.control.fallback import FallbackNode
        from bteng.nodes.control.parallel import ParallelNode
        from bteng.nodes.control.reactive_sequence import ReactiveSequenceNode
        from bteng.nodes.control.reactive_fallback import ReactiveFallbackNode
        from bteng.nodes.decorators.inverter import Inverter
        from bteng.nodes.decorators.retry import Retry
        from bteng.nodes.decorators.timeout import Timeout
        from bteng.nodes.decorators.rate_controller import RateController
        from bteng.nodes.decorators.force_result import ForceSuccess, ForceFailure
        from bteng.nodes.subtree import SubTree

        builtins: Dict[str, Type[TreeNode]] = {
            "Sequence":          SequenceNode,
            "Fallback":          FallbackNode,
            "Selector":          FallbackNode,
            "Parallel":          ParallelNode,
            "ReactiveSequence":  ReactiveSequenceNode,
            "ReactiveFallback":  ReactiveFallbackNode,
            "Inverter":          Inverter,
            "Retry":             Retry,
            "Timeout":           Timeout,
            "RateController":    RateController,
            "ForceSuccess":      ForceSuccess,
            "ForceFailure":      ForceFailure,
            "SubTree":           SubTree,
        }
        for name, cls in builtins.items():
            self.register(cls, name)


# ── Decorator helper ──────────────────────────────────────────────────────────

def register_node(name: Optional[str] = None) -> Callable:
    """Class decorator to register a node with the global factory.

    Usage::

        @register_node()
        class MyAction(ActionNode):
            def tick(self):
                return NodeStatus.SUCCESS

        @register_node("my_alias")
        class AnotherAction(ActionNode):
            ...

    The registration is also recorded module-side and replayed onto any fresh
    singleton, so ``NodeFactory.reset_instance()`` does not permanently lose
    decorator-registered nodes (module-level decorators run only once per
    interpreter).  Note it still targets the *global* singleton: a factory built
    directly as ``NodeFactory()`` starts with built-ins only.
    """
    def decorator(cls: Type[TreeNode]) -> Type[TreeNode]:
        _DECORATOR_REGISTRATIONS.append((cls, name))
        NodeFactory.get_instance().register(cls, name)
        return cls
    return decorator
