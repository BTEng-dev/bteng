"""Condition leaf nodes."""

from __future__ import annotations

from typing import Any, Callable, Optional

from bteng.core.node import LeafNode, NodeConfig, NodeStatus, NodeType


class ConditionNode(LeafNode):
    """Base class for condition nodes. Subclass and override tick()."""

    node_type = NodeType.CONDITION

    def __init__(self, name: str, config: Optional[NodeConfig] = None):
        super().__init__(name, config)

    def tick(self) -> NodeStatus:
        raise NotImplementedError(f"{type(self).__name__}.tick() not implemented")


class FunctionCondition(ConditionNode):
    """Wraps a callable as a ConditionNode.

    The callable receives ``self`` and returns bool or NodeStatus.
    """

    def __init__(
        self,
        name: str,
        fn: Callable[["FunctionCondition"], Any],
        config: Optional[NodeConfig] = None,
    ):
        super().__init__(name, config)
        self._fn = fn

    def tick(self) -> NodeStatus:
        result = self._fn(self)
        if isinstance(result, NodeStatus):
            return result
        return NodeStatus.SUCCESS if result else NodeStatus.FAILURE


def condition(name: str, fn: Callable) -> FunctionCondition:
    """Convenience factory for inline condition nodes."""
    return FunctionCondition(name, fn)
