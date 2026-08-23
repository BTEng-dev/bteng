"""ForceSuccess / ForceFailure decorators."""

from __future__ import annotations

from bteng.core.node import DecoratorNode, NodeStatus


class ForceSuccess(DecoratorNode):
    """Always return SUCCESS (unless child is RUNNING)."""

    def __init__(self, name: str = "ForceSuccess", child=None, config=None):
        super().__init__(name, child, config)

    def tick(self) -> NodeStatus:
        status = self._child.execute_tick()
        if status == NodeStatus.RUNNING:
            return NodeStatus.RUNNING
        return NodeStatus.SUCCESS


class ForceFailure(DecoratorNode):
    """Always return FAILURE (unless child is RUNNING)."""

    def __init__(self, name: str = "ForceFailure", child=None, config=None):
        super().__init__(name, child, config)

    def tick(self) -> NodeStatus:
        status = self._child.execute_tick()
        if status == NodeStatus.RUNNING:
            return NodeStatus.RUNNING
        return NodeStatus.FAILURE
