"""Fluent C++-style API for constructing behavior trees programmatically."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from bteng.core.node import (
    ControlNode, DecoratorNode, LeafNode, NodeConfig,
    NodeStatus, NodeType, PortDefinition, TreeNode,
)
from bteng.core.tree import Tree, TreeMetadata
from bteng.blackboard.blackboard import Blackboard


# ── Stack frame ───────────────────────────────────────────────────────────────

@dataclass
class _BuildFrame:
    node:     TreeNode
    next_id:  int = 0  # unused locally; global counter used by builder


# ── TreeBuilder ───────────────────────────────────────────────────────────────

class TreeBuilder:
    """Fluent builder for constructing behavior trees in pure Python.

    Mirrors the C++ TreeBuilder stack-based pattern.  Each control/decorator
    call opens a scope; end() closes it and attaches the completed subtree to
    its parent.

    PORT MAPPING
    ------------
    map(port, key)      binds port to a blackboard key on the **most recently
                        added node** (leaf or scope opener).
    literal(port, val)  binds port to a static value on the same node.
    Call immediately after the node they apply to.

    STACK SEMANTICS
    ---------------
    sequence / fallback / parallel / etc. push onto the builder stack.
    Subsequent node() / action() / condition() calls attach to the top.
    end() pops the top node and attaches it as a child of the new top.
    build() finalises: the stack must be empty (all scopes closed).
    """

    def __init__(
        self,
        factory:    Optional[Any]        = None,  # NodeFactory
        blackboard: Optional[Blackboard] = None,
    ) -> None:
        if factory is None:
            from bteng.factory.factory import NodeFactory
            factory = NodeFactory.get_instance()
        self._factory    = factory
        self._blackboard = blackboard
        self._meta       = TreeMetadata()
        self._stack:      List[_BuildFrame]    = []
        self._root:       Optional[TreeNode]   = None
        self._last_node:  Optional[TreeNode]   = None  # most recently added node
        self._next_id:    int = 1

    # ── Tree-level metadata ───────────────────────────────────────────────────

    def tree_id(self, id_: str) -> "TreeBuilder":
        self._meta.id = id_
        return self

    def description(self, desc: str) -> "TreeBuilder":
        self._meta.description = desc
        return self

    def version(self, v: str) -> "TreeBuilder":
        self._meta.version = v
        return self

    # ── Control node scopes ───────────────────────────────────────────────────

    def sequence(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.control.sequence import SequenceNode
        n = SequenceNode(name or self._auto_name("Sequence"), children=[],
                         config=self._make_config())
        self._push_frame(n)
        return self

    def fallback(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.control.fallback import FallbackNode
        n = FallbackNode(name or self._auto_name("Fallback"), children=[],
                         config=self._make_config())
        self._push_frame(n)
        return self

    def parallel(
        self, name: str = "",
        success_threshold: int = -1,
        failure_threshold: int = 1,
    ) -> "TreeBuilder":
        from bteng.nodes.control.parallel import ParallelNode
        n = ParallelNode(
            name or self._auto_name("Parallel"), children=[],
            config=self._make_config(),
            success_threshold=success_threshold,
            failure_threshold=failure_threshold,
        )
        self._push_frame(n)
        return self

    def reactive_sequence(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.control.reactive_sequence import ReactiveSequenceNode
        n = ReactiveSequenceNode(name or self._auto_name("ReactiveSequence"),
                                 children=[], config=self._make_config())
        self._push_frame(n)
        return self

    def reactive_fallback(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.control.reactive_fallback import ReactiveFallbackNode
        n = ReactiveFallbackNode(name or self._auto_name("ReactiveFallback"),
                                 children=[], config=self._make_config())
        self._push_frame(n)
        return self

    # ── Decorator scopes ──────────────────────────────────────────────────────

    def inverter(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.decorators.inverter import Inverter
        from bteng.nodes.leaf.action import FunctionAction
        placeholder = FunctionAction("__placeholder__", lambda _: NodeStatus.SUCCESS)
        n = Inverter(name or self._auto_name("Inverter"), child=placeholder,
                     config=self._make_config())
        self._push_frame(n)
        return self

    def retry(self, max_attempts: int = 3, name: str = "") -> "TreeBuilder":
        from bteng.nodes.decorators.retry import Retry
        from bteng.nodes.leaf.action import FunctionAction
        placeholder = FunctionAction("__placeholder__", lambda _: NodeStatus.SUCCESS)
        n = Retry(name or self._auto_name("Retry"), child=placeholder,
                  config=self._make_config(), max_attempts=max_attempts)
        self._push_frame(n)
        return self

    def timeout(self, msec: float = 1000.0, name: str = "") -> "TreeBuilder":
        from bteng.nodes.decorators.timeout import Timeout
        from bteng.nodes.leaf.action import FunctionAction
        placeholder = FunctionAction("__placeholder__", lambda _: NodeStatus.SUCCESS)
        n = Timeout(name or self._auto_name("Timeout"), child=placeholder,
                    config=self._make_config(), duration=msec / 1000.0)
        self._push_frame(n)
        return self

    def force_success(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.decorators.force_result import ForceSuccess
        from bteng.nodes.leaf.action import FunctionAction
        placeholder = FunctionAction("__placeholder__", lambda _: NodeStatus.SUCCESS)
        n = ForceSuccess(name or self._auto_name("ForceSuccess"), child=placeholder,
                         config=self._make_config())
        self._push_frame(n)
        return self

    def force_failure(self, name: str = "") -> "TreeBuilder":
        from bteng.nodes.decorators.force_result import ForceFailure
        from bteng.nodes.leaf.action import FunctionAction
        placeholder = FunctionAction("__placeholder__", lambda _: NodeStatus.SUCCESS)
        n = ForceFailure(name or self._auto_name("ForceFailure"), child=placeholder,
                         config=self._make_config())
        self._push_frame(n)
        return self

    # ── Leaf nodes ────────────────────────────────────────────────────────────

    def node(
        self,
        type_name: str,
        node_name: str = "",
        attrs: Optional[Dict[str, Any]] = None,
    ) -> "TreeBuilder":
        """Add a node registered in the factory by type name."""
        name   = node_name or self._auto_name(type_name)
        config = self._make_config(params=attrs or {})
        n      = self._factory.create_leaf(type_name, name, config)
        self._attach_leaf(n)
        return self

    def action(
        self,
        name: str,
        fn:   Callable,
    ) -> "TreeBuilder":
        """Add a lambda action.

        ``fn`` can be zero-arg ``lambda: status`` or one-arg ``lambda node: status``.
        Both ``NodeStatus`` and ``bool`` return values are accepted.
        """
        from bteng.nodes.leaf.action import FunctionAction
        n = FunctionAction(name, self._adapt_fn(fn), config=self._make_config())
        self._attach_leaf(n)
        return self

    def condition(
        self,
        name: str,
        fn:   Callable,
    ) -> "TreeBuilder":
        """Add a lambda condition.

        ``fn`` can be zero-arg or one-arg (receives node).
        """
        from bteng.nodes.leaf.condition import FunctionCondition
        n = FunctionCondition(name, self._adapt_fn(fn), config=self._make_config())
        self._attach_leaf(n)
        return self

    # ── Port mapping ──────────────────────────────────────────────────────────

    def map(self, port: str, blackboard_key: str) -> "TreeBuilder":
        """Bind an input port to a blackboard key on the most recently added node."""
        if self._last_node is not None:
            self._last_node._config.input_ports[port] = blackboard_key
        return self

    def map_output(self, port: str, blackboard_key: str) -> "TreeBuilder":
        """Bind an output port to a blackboard key on the most recently added node."""
        if self._last_node is not None:
            self._last_node._config.output_ports[port] = blackboard_key
        return self

    def literal(self, port: str, value: Any) -> "TreeBuilder":
        """Bind a port to a static literal value on the most recently added node."""
        if self._last_node is not None:
            self._last_node._config.params[port] = value
        return self

    # ── Scope close ───────────────────────────────────────────────────────────

    def end(self) -> "TreeBuilder":
        """Pop the current scope and attach it to its parent.

        Must be called once for every sequence/fallback/parallel/etc. call.
        """
        if not self._stack:
            raise RuntimeError("TreeBuilder.end() called with empty stack")

        frame = self._stack.pop()
        node  = frame.node

        # Decorator nodes require exactly one real child
        if isinstance(node, DecoratorNode):
            if hasattr(node, "_child") and node._child.name == "__placeholder__":
                raise RuntimeError(
                    f"TreeBuilder.end(): decorator '{node.name}' has no child. "
                    "Add a child node inside the decorator scope before calling end()."
                )

        if self._stack:
            top = self._stack[-1].node
            if hasattr(top, "_children"):
                top._children.append(node)
            elif hasattr(top, "_child"):
                top._child = node
            self._last_node = self._stack[-1].node
        else:
            self._root = node
            self._last_node = node

        return self

    # ── Finalise ─────────────────────────────────────────────────────────────

    def build(self) -> Tree:
        """Consume the builder and produce a Tree.

        Raises RuntimeError if any scopes were not closed with end().
        """
        if self._stack:
            unclosed = [f.node.name for f in self._stack]
            raise RuntimeError(
                f"TreeBuilder.build(): unclosed scopes: {unclosed}. "
                "Call end() for each sequence/fallback/parallel/decorator."
            )
        if self._root is None:
            raise RuntimeError("TreeBuilder.build(): no root node defined")

        # Blackboard(...) rather than Blackboard.create(...): create() memoises
        # by name, so every builder-made tree without a tree_id() would share
        # one "__tree__" blackboard and start life holding the previous tree's
        # keys.
        bb = self._blackboard or Blackboard(scope_name=self._meta.id or "__tree__")
        return Tree(self._meta, self._root, bb)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _auto_name(self, type_name: str) -> str:
        name = f"{type_name}_{self._next_id}"
        self._next_id += 1
        return name

    def _make_config(self, params: Optional[Dict[str, Any]] = None) -> NodeConfig:
        return NodeConfig(
            blackboard=self._blackboard,
            params=params or {},
            node_id=self._next_id,
        )

    def _push_frame(self, node: TreeNode) -> None:
        self._last_node = node
        self._stack.append(_BuildFrame(node=node))

    def _attach_leaf(self, node: TreeNode) -> None:
        """Attach a leaf node to the top of the stack and track it as last node."""
        self._last_node = node
        if self._stack:
            top = self._stack[-1].node
            if hasattr(top, "_children"):
                top._children.append(node)
            elif hasattr(top, "_child"):
                top._child = node
        else:
            self._root = node

    @staticmethod
    def _adapt_fn(fn: Callable) -> Callable[[Any], NodeStatus]:
        """Adapt a callable to the (node) → NodeStatus signature.

        Accepts both zero-arg lambdas and one-arg (node) callables.
        ``bool`` returns are coerced: True → SUCCESS, False → FAILURE.
        """
        try:
            sig = inspect.signature(fn)
            required = [
                p for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind not in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
            ]
            accepts_node = len(required) >= 1
        except (ValueError, TypeError):
            accepts_node = False

        def adapted(node: Any) -> NodeStatus:
            result = fn(node) if accepts_node else fn()
            if isinstance(result, NodeStatus):
                return result
            return NodeStatus.SUCCESS if result else NodeStatus.FAILURE

        return adapted
