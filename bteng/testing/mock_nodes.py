"""Mock and simulated nodes for unit testing behavior trees."""
from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass
from typing import Callable, Optional

from bteng.core.node import NodeConfig, NodeStatus
from bteng.nodes.leaf.action import ActionNode
from bteng.nodes.leaf.condition import ConditionNode
from bteng.nodes.leaf.stateful_action import StatefulActionNode


# ── Migration shim ────────────────────────────────────────────────────────────

class _CallableInt(int):
    """An ``int`` that also survives being called.

    ``tick_count_local`` used to be a method and is now a property, matching
    ``TreeNode.tick_count``. Returning this instead of a plain ``int`` means both
    ``mock.tick_count_local`` and the old ``mock.tick_count_local()`` work; the
    call form warns and will be removed in a future release.
    """

    __slots__ = ()

    def __call__(self) -> "int":
        warnings.warn(
            "tick_count_local is now a property; drop the parentheses.",
            DeprecationWarning, stacklevel=2,
        )
        return int(self)


# ── MockActionNode ────────────────────────────────────────────────────────────

class MockActionNode(ActionNode):
    """Configurable mock action: returns specified status after N ticks.

    Useful for unit tests that need predictable node behavior.

    Usage::

        mock = MockActionNode("MyAction")
        mock.set_result(NodeStatus.FAILURE)
        mock.set_ticks_to_complete(3)   # returns RUNNING for 2 ticks, then FAILURE
    """

    def __init__(self, name: str = "MockAction", config: Optional[NodeConfig] = None) -> None:
        super().__init__(name, config)
        self._forced_result:    NodeStatus                      = NodeStatus.SUCCESS
        self._ticks_needed:     int                             = 1
        self._ticks_done:       int                             = 0
        self._callback:         Optional[Callable[[], NodeStatus]] = None
        self._tick_count_local: int                             = 0
        self._lock              = threading.Lock()

    def set_status(self, status: NodeStatus) -> None:
        """Choose the NodeStatus this mock settles on (default SUCCESS)."""
        self._forced_result = status

    def set_result(self, status: NodeStatus) -> None:
        """Deprecated alias of :meth:`set_status`.

        Renamed because ``MockConditionNode.set_result`` takes a ``bool`` while
        this one takes a ``NodeStatus`` — one name, two incompatible types.
        """
        warnings.warn(
            "MockActionNode.set_result() is deprecated; use set_status() instead.",
            DeprecationWarning, stacklevel=2,
        )
        self.set_status(status)

    def set_ticks_to_complete(self, n: int) -> None:
        """Return RUNNING for (n-1) ticks, then the forced result."""
        self._ticks_needed = n

    def set_callback(self, fn: Callable[[], NodeStatus]) -> None:
        """Override tick logic entirely."""
        self._callback = fn

    @property
    def tick_count_local(self) -> "_CallableInt":
        """Number of times this mock's own tick() ran.

        A property, matching ``TreeNode.tick_count``. Calling it like a method
        still works so existing tests keep passing, but that form is deprecated.
        """
        with self._lock:
            return _CallableInt(self._tick_count_local)

    def reset_count(self) -> None:
        with self._lock:
            self._tick_count_local = 0
            self._ticks_done = 0

    def tick(self) -> NodeStatus:
        with self._lock:
            self._tick_count_local += 1

        if self._callback is not None:
            return self._callback()

        self._ticks_done += 1
        if self._ticks_done < self._ticks_needed:
            return NodeStatus.RUNNING
        self._ticks_done = 0
        return self._forced_result

    def _on_halt(self) -> None:
        with self._lock:
            self._ticks_done = 0

    def _on_reset(self) -> None:
        with self._lock:
            self._ticks_done = 0


# ── MockConditionNode ─────────────────────────────────────────────────────────

class MockConditionNode(ConditionNode):
    """Configurable mock condition node.

    Usage::

        cond = MockConditionNode("IsBatteryOk")
        cond.set_result(True)   # or NodeStatus.SUCCESS
    """

    def __init__(self, name: str = "MockCondition", config: Optional[NodeConfig] = None) -> None:
        super().__init__(name, config)
        self._result:         bool                          = True
        self._callback:       Optional[Callable[[], bool]] = None
        self._tick_count_local: int                        = 0
        self._lock            = threading.Lock()

    def set_bool(self, success: bool) -> None:
        """Choose the boolean this condition reports (True -> SUCCESS)."""
        self._result = success

    def set_result(self, success: bool) -> None:
        """Deprecated alias of :meth:`set_bool`. See MockActionNode.set_result."""
        warnings.warn(
            "MockConditionNode.set_result() is deprecated; use set_bool() instead.",
            DeprecationWarning, stacklevel=2,
        )
        self.set_bool(success)

    def set_callback(self, fn: Callable[[], bool]) -> None:
        self._callback = fn

    @property
    def tick_count_local(self) -> "_CallableInt":
        """Number of times this mock's own tick() ran. See MockActionNode."""
        with self._lock:
            return _CallableInt(self._tick_count_local)

    def tick(self) -> NodeStatus:
        with self._lock:
            self._tick_count_local += 1

        result = self._callback() if self._callback else self._result
        return NodeStatus.SUCCESS if result else NodeStatus.FAILURE


# ── SimulatedActionNode ───────────────────────────────────────────────────────

@dataclass
class SimConfig:
    """Configuration for SimulatedActionNode."""
    delay_ticks:             int         = 0
    result:                  NodeStatus  = NodeStatus.SUCCESS
    force_failure_injection: bool        = False


class SimulatedActionNode(StatefulActionNode):
    """Three-phase action with simulated delay and configurable outcomes.

    Used in simulation-mode testing where timing is controlled by tick count
    rather than wall-clock time.

    Usage::

        sim = SimulatedActionNode("Navigate", SimConfig(delay_ticks=5))
        # Returns RUNNING for 5 ticks, then SUCCESS
    """

    def __init__(
        self,
        name:   str,
        sim:    SimConfig,
        config: Optional[NodeConfig] = None,
    ) -> None:
        super().__init__(name, config)
        self._sim          = sim
        self._ticks_elapsed = 0

    def on_start(self) -> NodeStatus:
        self._ticks_elapsed = 0
        if self._sim.delay_ticks == 0:
            return (NodeStatus.FAILURE if self._sim.force_failure_injection
                    else self._sim.result)
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        self._ticks_elapsed += 1
        if self._ticks_elapsed >= self._sim.delay_ticks:
            return (NodeStatus.FAILURE if self._sim.force_failure_injection
                    else self._sim.result)
        return NodeStatus.RUNNING

    def on_halted(self) -> None:
        self._ticks_elapsed = 0
