"""Built-in leaf nodes — blackboard utilities and status stubs."""
from __future__ import annotations

from bteng.core.node import InputPort, NodeStatus, PortDefinition
from bteng.nodes.leaf.action import ActionNode
from bteng.nodes.leaf.condition import ConditionNode
from typing import List


class AlwaysSuccess(ActionNode):
    """Always returns SUCCESS. Useful as a placeholder or default branch."""

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class AlwaysFailure(ActionNode):
    """Always returns FAILURE. Useful for forcing re-evaluation or testing."""

    def tick(self) -> NodeStatus:
        return NodeStatus.FAILURE


class AlwaysRunning(ActionNode):
    """Always returns RUNNING. Useful for blocking a branch indefinitely."""

    def tick(self) -> NodeStatus:
        return NodeStatus.RUNNING


class SetBlackboard(ActionNode):
    """Write a static value to a blackboard key.

    Ports:
        key   (input) — blackboard key to write (string)
        value (input) — value to write
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            InputPort("key",   "Blackboard key to write"),
            InputPort("value", "Value to write"),
        ]

    def tick(self) -> NodeStatus:
        key = self.get_input("key")
        if key is None:
            self.set_feedback_message("port 'key' not set")
            return NodeStatus.FAILURE
        if self._config.blackboard is None:
            self.set_feedback_message("no blackboard attached")
            return NodeStatus.FAILURE
        value = self.get_input("value")
        self._config.blackboard.set(key, value, writer=self.uid)
        self.set_feedback_message(f"{key} = {value!r}")
        return NodeStatus.SUCCESS


class CheckBlackboard(ConditionNode):
    """Return SUCCESS if a blackboard key exists and is not None.

    Ports:
        key (input) — blackboard key to check
    """

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [
            InputPort("key", "Blackboard key to check"),
        ]

    def tick(self) -> NodeStatus:
        key = self.get_input("key")
        if key is None:
            self.set_feedback_message("port 'key' not set")
            return NodeStatus.FAILURE
        if self._config.blackboard is None or not self._config.blackboard.has(key):
            self.set_feedback_message(f"{key!r} not set")
            return NodeStatus.FAILURE
        value = self._config.blackboard.get(key)
        self.set_feedback_message(f"{key!r} = {value!r}")
        return NodeStatus.SUCCESS
