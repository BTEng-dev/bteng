"""Sequence control node."""

from __future__ import annotations

from bteng.core.node import ControlNode, NodeStatus


class SequenceNode(ControlNode):
    """Tick children left-to-right.

    * Child SUCCESS → advance to next child.
    * Child RUNNING → return RUNNING (resume next tick).
    * Child FAILURE → halt all, return FAILURE.
    * All children SUCCESS → return SUCCESS.
    """

    def __init__(self, name: str = "Sequence", children=None, config=None):
        super().__init__(name, children or [], config)
        self._current_idx: int = 0

    def tick(self) -> NodeStatus:
        while self._current_idx < len(self._children):
            child = self._children[self._current_idx]
            status = child.execute_tick()

            if status == NodeStatus.RUNNING:
                return NodeStatus.RUNNING

            if status == NodeStatus.FAILURE:
                self._halt_children()
                self._current_idx = 0
                return NodeStatus.FAILURE

            # SUCCESS → advance
            self._current_idx += 1

        self._current_idx = 0
        return NodeStatus.SUCCESS

    def halt(self) -> None:
        super().halt()
        self._current_idx = 0
