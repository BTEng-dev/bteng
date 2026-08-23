"""Parallel control node."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple

from bteng.core.node import ControlNode, NodeStatus, TreeNode


# ── ParallelPolicy ────────────────────────────────────────────────────────────

class ParallelPolicy(Enum):
    """Controls when ParallelNode decides to return SUCCESS or FAILURE.

    Each policy fixes *both* thresholds — a policy that only pinned
    success_threshold left failure_threshold at its default of 1, so the first
    child to fail aborted the node no matter what the policy promised.

    REQUIRE_ALL_SUCCESS   — SUCCESS only when every child has succeeded;
                            the first FAILURE aborts.
                            Thresholds: success=n, failure=1.
    REQUIRE_ONE_SUCCESS   — SUCCESS as soon as any single child succeeds.
                            Failing children do NOT abort the node; the others
                            keep running and can still succeed.  FAILURE only
                            once every child has failed.  success_threshold is
                            ignored.  Thresholds: success=1, failure=n.
    REQUIRE_ALL_COMPLETE  — Wait for every child to finish, then decide with
                            success_threshold (<= 0 = all must succeed).
                            Nothing is decided — and no still-running child is
                            halted — before every child has completed.
    """
    REQUIRE_ALL_SUCCESS  = "require_all_success"
    REQUIRE_ONE_SUCCESS  = "require_one_success"
    REQUIRE_ALL_COMPLETE = "require_all_complete"


# ── ParallelNode ──────────────────────────────────────────────────────────────

class ParallelNode(ControlNode):
    """Tick ALL children every tick simultaneously.

    Args:
        success_threshold:
            How many children must succeed for overall SUCCESS.
            Any value <= 0 (the default is -1) means *all children*, recomputed
            each tick from the live child count, so dynamic INSERT/REMOVE is
            safe.  A value larger than the child count is clamped down to it.
            Ignored when policy=REQUIRE_ONE_SUCCESS.
            Also a declared input port, re-read every tick, so a *programmatic*
            remap -- ``NodeConfig(input_ports={"success_threshold": "max_ok"})``
            -- resolves against the blackboard.  Note this is NOT reachable from
            XML: the parser takes control-node attributes as constructor
            arguments, so ``<Parallel success_threshold="{max_ok}"/>`` is
            rejected as "must be a literal number".
        failure_threshold:
            How many children must fail for overall FAILURE. Default = 1.
            Clamped into [1, child count].  (Not a declared port — see
            provided_ports().)
        policy:
            ParallelPolicy enum controlling result computation.  When provided
            it determines BOTH thresholds; see ParallelPolicy.

    Both thresholds are clamped to the live child count.  Without that clamp a
    threshold larger than the number of children could never be met, and since
    tick() does not re-tick a child that already reached SUCCESS/FAILURE the
    node stayed RUNNING forever with nothing left to run.  For the same reason
    the node decides (SUCCESS if the success threshold was met, else FAILURE)
    as soon as every child has completed.

    With no children the node returns SUCCESS: "all children succeeded" is
    vacuously true, which is the documented meaning of the default threshold,
    and it matches SequenceNode([]) → SUCCESS.  (FallbackNode([]) → FAILURE
    because there its default is "some child must succeed".)
    """

    def __init__(
        self,
        name:               str                         = "Parallel",
        children:           Optional[List[TreeNode]]   = None,
        config=None,
        success_threshold:  int                         = -1,
        failure_threshold:  int                         = 1,
        policy:             Optional[ParallelPolicy]   = None,
    ) -> None:
        super().__init__(name, children or [], config)
        self._policy                = policy
        self._failure_threshold     = failure_threshold
        # Store the raw threshold; -1 means "use all children" (resolved lazily in tick()).
        # This avoids stale threshold when children are added/removed after construction.
        self._raw_success_threshold = success_threshold

    def _raw_threshold_from_port(self) -> int:
        """Resolve the success_threshold input port, falling back to the ctor value.

        success_threshold is a declared input port, so it may be remapped to a
        blackboard key via NodeConfig(input_ports=...).  Reading it here is what
        makes that mapping take effect; previously the port was decorative and
        only the constructor argument counted.  XML cannot reach this path --
        parser._control_kwargs needs an int at build time and rejects a {ref}.
        """
        raw = self.get_input("success_threshold", self._raw_success_threshold)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return self._raw_success_threshold

    def _effective_thresholds(self) -> Tuple[int, int]:
        """Return (success_threshold, failure_threshold) for the live child count.

        Both are clamped to the number of children; an unreachable threshold is
        what used to livelock the node.
        """
        n = len(self._children)
        if n == 0:
            return 0, 0

        if self._policy is ParallelPolicy.REQUIRE_ONE_SUCCESS:
            # One success wins; only an all-failure outcome loses.
            return 1, n
        if self._policy is ParallelPolicy.REQUIRE_ALL_SUCCESS:
            return n, 1
        if self._policy is ParallelPolicy.REQUIRE_ALL_COMPLETE:
            raw = self._raw_threshold_from_port()
            success = n if raw <= 0 else min(raw, n)
            # Never abort early: tick() gates this policy on completion anyway.
            return success, n

        raw = self._raw_threshold_from_port()
        success = n if raw <= 0 else min(raw, n)          # <= 0 means "all children"
        failure = min(max(self._failure_from_port(), 1), n)  # 0/negative fails instantly
        return success, failure

    def _failure_from_port(self) -> int:
        """failure_threshold from the blackboard, else the constructor value."""
        raw = self.get_input("failure_threshold", self._failure_threshold)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return self._failure_threshold

    # Kept for backward compatibility with anything that introspected it.
    def _effective_success_threshold(self) -> int:
        return self._effective_thresholds()[0]

    def tick(self) -> NodeStatus:
        n = len(self._children)
        if n == 0:
            # Vacuous: "all zero children succeeded".  Returning RUNNING here
            # (what REQUIRE_ONE_SUCCESS used to do) can never be escaped.
            return NodeStatus.SUCCESS

        success_threshold, failure_threshold = self._effective_thresholds()

        success_count = 0
        failure_count = 0
        for child in self._children:
            if child.status == NodeStatus.SUCCESS:
                success_count += 1
                continue
            if child.status == NodeStatus.FAILURE:
                failure_count += 1
                continue

            child_status = child.execute_tick()
            if child_status == NodeStatus.SUCCESS:
                success_count += 1
            elif child_status == NodeStatus.FAILURE:
                failure_count += 1

        completed = success_count + failure_count

        if self._policy is ParallelPolicy.REQUIRE_ALL_COMPLETE:
            # "Wait for every child to finish" — no early exit in either
            # direction, so a still-running child is never halted mid-flight.
            if completed < n:
                return NodeStatus.RUNNING
            return self._finish(success_count >= success_threshold)

        if success_count >= success_threshold:
            return self._finish(True)

        if failure_count >= failure_threshold:
            return self._finish(False)

        if completed >= n:
            # Every child is terminal and neither threshold fired.  Nothing will
            # ever change (completed children are not re-ticked), so decide now
            # instead of hanging: the success threshold was not reached.
            return self._finish(False)

        return NodeStatus.RUNNING

    def _finish(self, succeeded: bool) -> NodeStatus:
        self._halt_running_children()
        self._reset_children_status()
        return NodeStatus.SUCCESS if succeeded else NodeStatus.FAILURE

    def halt(self) -> None:
        if self._status == NodeStatus.RUNNING:
            self._on_halt()
            # This override does not call super().halt(), so the inspector
            # notification has to happen here too or a halted Parallel stays in
            # running_nodes() forever.
            self._notify_halt()
        self._halt_running_children()
        self._reset_children_status()
        self._status = NodeStatus.IDLE

    def _halt_running_children(self) -> None:
        for child in self._children:
            if child.status == NodeStatus.RUNNING:
                child.halt()

    def _reset_children_status(self) -> None:
        """Reset completed (non-RUNNING) children — and their subtrees — to IDLE.

        This used to call ``child._on_reset()`` on the child only, despite the
        comment claiming reset_node().  Nodes that keep activation state outside
        ``_status`` and only clear it in ``_on_halt()`` (RateController's
        _last_tick/_last_status, Timeout's _start_time) therefore came back
        stale on the next activation: RateController replayed its previous
        status without ticking its child at all.  Descendants were missed too,
        so a SequenceNode grandchild kept its _current_idx.
        """
        for child in self._children:
            if child.status in (NodeStatus.RUNNING, NodeStatus.IDLE):
                continue
            self._reset_subtree(child)

    @staticmethod
    def _reset_subtree(node: TreeNode) -> None:
        """TreeNode.reset_node(), but with _on_halt() gated on RUNNING.

        reset_node() calls _on_halt() unconditionally, so using it verbatim
        would deliver on_halted() to every StatefulActionNode in the subtree
        that finished SUCCESS — an event StatefulActionNode documents as
        "called when the node is externally halted (was RUNNING)", and which a
        ROS action node implements as "cancel the goal".  Cancelling a goal
        that already succeeded is worse than the stale state we are clearing,
        so _on_halt() fires only for nodes that really are RUNNING (the callers
        run _halt_running_children() first, so normally none are).
        """
        if node.status is NodeStatus.RUNNING:
            node._on_halt()
        node._on_reset()
        node._status           = NodeStatus.IDLE
        node._feedback_message = ""
        for grandchild in node.get_children():
            ParallelNode._reset_subtree(grandchild)

    @classmethod
    def provided_ports(cls):
        from bteng.core.node import InputPort
        # The default must be declared here, not only as a constructor argument:
        # validate_node() treats a defaultless input port as required, so without
        # it ParallelNode("p", children=[...]) — and a bare <Parallel> in XML —
        # were rejected at set_tree() even though -1 ("all children") is the
        # documented default.
        # failure_threshold is deliberately NOT declared: validate_node() rejects
        # a mapping to an undeclared port, so declaring it is the only way to
        # make failure_threshold="{...}" legal — see the report.
        return [InputPort("success_threshold",
                          "Min successes for overall SUCCESS (<=0 = all children)",
                          default=-1)]
