"""Timeout decorator."""

from __future__ import annotations

from typing import Optional

from bteng.concurrency.clock import Clock, WallClock
from bteng.core.node import DecoratorNode, NodeStatus


class Timeout(DecoratorNode):
    """Halt child and return FAILURE if it runs longer than *duration* seconds.

    ``duration`` is a declared input port, re-read every tick, so
    ``<Timeout duration="{budget}"/>`` resolves against the blackboard.
    ``msec`` remains a build-time literal (it is converted to seconds by the
    parser); use ``duration`` for the blackboard form.
    """

    def __init__(
        self,
        name: str = "Timeout",
        child=None,
        config=None,
        duration: float = 1.0,
        clock: Optional[Clock] = None,
    ):
        super().__init__(name, child, config)
        self._duration = duration
        self._clock: Clock = clock or WallClock()
        self._start_time: Optional[float] = None

    @classmethod
    def provided_ports(cls):
        from bteng.core.node import InputPort

        return [InputPort("duration", "Seconds the child may run before it is halted",
                          default=1.0)]

    def _budget(self) -> float:
        raw = self.get_input("duration", self._duration)
        try:
            return float(raw)
        except (TypeError, ValueError):
            self.set_feedback_message(
                f"duration={raw!r} is not a number; using {self._duration}"
            )
            return self._duration

    def tick(self) -> NodeStatus:
        duration = self._budget()
        now = self._clock.monotonic()
        if self._status != NodeStatus.RUNNING:
            self._start_time = now

        if now - self._start_time > duration:  # type: ignore[operator]
            self._child.halt()
            return NodeStatus.FAILURE

        return self._child.execute_tick()

    def _on_halt(self) -> None:
        self._start_time = None
