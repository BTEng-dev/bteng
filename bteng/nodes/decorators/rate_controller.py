"""RateController decorator."""

from __future__ import annotations

from typing import Optional

from bteng.concurrency.clock import Clock, WallClock
from bteng.core.node import DecoratorNode, NodeStatus


class RateController(DecoratorNode):
    """Limit how often the child is re-ticked (in Hz).

    Between allowed ticks, returns the last known status without re-ticking.

    ``hz`` is a declared input port, re-read every tick, so
    ``<RateController hz="{rate}"/>`` resolves against the blackboard. A
    non-positive rate keeps the constructor's period rather than dividing by
    zero several layers away from whatever produced it.
    """

    def __init__(
        self,
        name: str = "RateController",
        child=None,
        config=None,
        hz: float = 1.0,
        clock: Optional[Clock] = None,
    ):
        super().__init__(name, child, config)
        self._ctor_period = 1.0 / hz
        self._clock: Clock = clock or WallClock()
        self._last_tick: Optional[float] = None
        self._last_status: NodeStatus = NodeStatus.IDLE

    @classmethod
    def provided_ports(cls):
        from bteng.core.node import InputPort

        return [InputPort("hz", "How many times per second the child may tick", default=1.0)]

    def _period(self) -> float:
        raw = self.get_input("hz", None)
        if raw is None:
            return self._ctor_period
        try:
            hz = float(raw)
        except (TypeError, ValueError):
            self.set_feedback_message(f"hz={raw!r} is not a number; keeping the current rate")
            return self._ctor_period
        if not hz > 0:
            self.set_feedback_message(f"hz={hz} is not positive; keeping the current rate")
            return self._ctor_period
        return 1.0 / hz

    def tick(self) -> NodeStatus:
        period = self._period()
        now = self._clock.monotonic()
        if self._last_tick is None or (now - self._last_tick) >= period:
            self._last_tick = now
            self._last_status = self._child.execute_tick()
        return self._last_status

    def _on_halt(self) -> None:
        self._last_tick = None
        self._last_status = NodeStatus.IDLE

    def _on_reset(self) -> None:
        """Clear the rate-limiter state on reset, not just on halt.

        _last_tick/_last_status live outside _status, so a parent that resets a
        completed child without halting it (ParallelNode._reset_children_status,
        Tree/Executor reset) left them set.  The next activation then replayed
        the previous status from the cache *without ticking the child*: the work
        silently did not happen and the parent was told it had succeeded.
        """
        self._last_tick = None
        self._last_status = NodeStatus.IDLE
