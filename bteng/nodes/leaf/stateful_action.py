"""Stateful action node."""

from __future__ import annotations

from bteng.core.node import NodeConfig, NodeStatus
from bteng.nodes.leaf.action import ActionNode


class StatefulActionNode(ActionNode):
    """Three-phase action node inspired by BehaviorTree.CPP.

    Override:
        on_start()   → called on first tick of each activation → return NodeStatus
        on_running() → called each tick while RUNNING           → return NodeStatus
        on_halted()  → called when the node is halted           → no return
    """

    def __init__(self, name: str, config: NodeConfig | None = None):
        super().__init__(name, config)

    # Because execute_tick() sets _status AFTER tick() returns,
    # inside tick() self._status still reflects the *previous* tick's result.
    def tick(self) -> NodeStatus:
        if self._status != NodeStatus.RUNNING:
            return self.on_start()
        return self.on_running()

    def _on_halt(self) -> None:
        self.on_halted()

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------

    def on_start(self) -> NodeStatus:
        """Called once when the node is first ticked. Return the initial status."""
        raise NotImplementedError(f"{type(self).__name__}.on_start() not implemented")

    def on_running(self) -> NodeStatus:
        """Called each tick while the node is RUNNING."""
        raise NotImplementedError(f"{type(self).__name__}.on_running() not implemented")

    def on_halted(self) -> None:
        """Called when the node is externally halted (was RUNNING)."""
