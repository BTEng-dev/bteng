"""Retry decorator."""

from __future__ import annotations

from bteng.core.node import DecoratorNode, NodeStatus


class Retry(DecoratorNode):
    """Re-try child on FAILURE up to *max_attempts* times.

    Returns RUNNING between attempts so the parent keeps ticking.
    Returns SUCCESS immediately when child succeeds.
    Returns FAILURE after exhausting all attempts.

    max_attempts <= 0 means "no attempts allowed": FAILURE without ticking the
    child at all.

    ``num_attempts`` is a declared input port, re-read every tick, so
    ``<Retry num_attempts="{max_tries}"/>`` resolves against the blackboard and
    the budget can change at runtime. Resolution order: blackboard mapping, then
    a literal XML attribute, then the constructor argument.
    """

    def __init__(
        self,
        name: str = "Retry",
        child=None,
        config=None,
        max_attempts: int = 3,
    ):
        super().__init__(name, child, config)
        self._max_attempts = max_attempts
        self._attempts: int = 0

    @classmethod
    def provided_ports(cls):
        from bteng.core.node import InputPort

        # Both spellings are accepted in XML, so both must be bindable; the
        # BT.CPP name wins when a tree sets both.
        return [
            InputPort("num_attempts", "How many times to retry the child", default=3),
            InputPort("max_attempts", "Alias of num_attempts", default=3),
        ]

    def _budget(self) -> int:
        """Attempts allowed this tick: blackboard, else XML literal, else ctor."""
        raw = self.get_input("num_attempts", None)
        if raw is None:
            raw = self.get_input("max_attempts", self._max_attempts)
        try:
            return int(raw)
        except (TypeError, ValueError):
            self.set_feedback_message(
                f"num_attempts={raw!r} is not an integer; using {self._max_attempts}"
            )
            return self._max_attempts

    def tick(self) -> NodeStatus:
        max_attempts = self._budget()
        # No attempts budgeted → do not run the child even once.  The counter is
        # incremented *after* the child runs, so 0 (or a negative maximum) used
        # to buy exactly one execution before reporting FAILURE.
        if max_attempts <= 0:
            self._attempts = 0
            return NodeStatus.FAILURE

        # Fresh start detected when our own status is not RUNNING
        if self._status != NodeStatus.RUNNING:
            self._attempts = 0

        child_status = self._child.execute_tick()

        if child_status == NodeStatus.SUCCESS:
            self._attempts = 0
            return NodeStatus.SUCCESS

        if child_status == NodeStatus.FAILURE:
            self._attempts += 1
            if self._attempts >= max_attempts:
                self._child.halt()
                return NodeStatus.FAILURE
            # Reset child for next attempt
            self._child.halt()
            return NodeStatus.RUNNING

        return NodeStatus.RUNNING  # child still running

    def _on_halt(self) -> None:
        self._attempts = 0
