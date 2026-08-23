"""Inverter decorator."""

from __future__ import annotations

from bteng.core.node import DecoratorNode, NodeStatus


class Inverter(DecoratorNode):
    """Flip SUCCESS ↔ FAILURE. Pass RUNNING through."""

    def __init__(self, name: str = "Inverter", child=None, config=None):
        super().__init__(name, child, config)

    def tick(self) -> NodeStatus:
        status = self._child.execute_tick()
        if status == NodeStatus.SUCCESS:
            return NodeStatus.FAILURE
        if status == NodeStatus.FAILURE:
            return NodeStatus.SUCCESS
        return status
