"""Action leaf nodes."""

from __future__ import annotations

from typing import Any, Callable, Optional

from bteng.core.node import LeafNode, NodeConfig, NodeStatus, NodeType


class ActionNode(LeafNode):
    """Base class for action nodes. Subclass and override tick()."""

    node_type = NodeType.ACTION

    def __init__(self, name: str, config: Optional[NodeConfig] = None):
        super().__init__(name, config)

    def tick(self) -> NodeStatus:
        raise NotImplementedError(f"{type(self).__name__}.tick() not implemented")


class FunctionAction(ActionNode):
    """Wraps a plain callable as an ActionNode.

    The callable receives ``self`` (the node) and returns either a
    :class:`NodeStatus` or a bool (True → SUCCESS, False → FAILURE).
    """

    def __init__(
        self,
        name: str,
        fn: Callable[["FunctionAction"], Any],
        config: Optional[NodeConfig] = None,
    ):
        super().__init__(name, config)
        self._fn = fn

    def tick(self) -> NodeStatus:
        result = self._fn(self)
        if isinstance(result, NodeStatus):
            return result
        return NodeStatus.SUCCESS if result else NodeStatus.FAILURE


def action(name: str, fn: Callable) -> FunctionAction:
    """Convenience factory for inline action nodes."""
    return FunctionAction(name, fn)
