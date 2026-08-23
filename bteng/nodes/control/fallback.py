"""Fallback (Selector) control node."""

from __future__ import annotations

from bteng.core.node import ControlNode, NodeStatus


class FallbackNode(ControlNode):
    """Tick children left-to-right.

    * Child FAILURE → advance to next child.
    * Child RUNNING → return RUNNING (resume next tick).
    * Child SUCCESS → halt remaining, return SUCCESS.
    * All children FAILURE → return FAILURE.
    """

    def __init__(self, name: str = "Fallback", children=None, config=None):
        super().__init__(name, children or [], config)
        self._current_idx: int = 0

    def tick(self) -> NodeStatus:
        while self._current_idx < len(self._children):
            child = self._children[self._current_idx]
            status = child.execute_tick()

            if status == NodeStatus.SUCCESS:
                self._halt_children()
                self._current_idx = 0
                return NodeStatus.SUCCESS

            if status == NodeStatus.RUNNING:
                return NodeStatus.RUNNING

            # FAILURE → advance
            self._current_idx += 1

        self._current_idx = 0
        return NodeStatus.FAILURE

    def halt(self) -> None:
        super().halt()
        self._current_idx = 0
