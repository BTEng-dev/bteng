"""Tests for true reactive execution (#3) — dirty-flag blackboard subscriptions."""
from __future__ import annotations

import pytest

from bteng import (
    NodeStatus, NodeConfig, Blackboard,
    ReactiveSequenceNode, ReactiveFallbackNode,
)
from bteng.testing.mock_nodes import MockActionNode, MockConditionNode


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bb(name: str) -> Blackboard:
    bb = Blackboard(scope_name=name)
    return bb


def _cfg(bb: Blackboard) -> NodeConfig:
    return NodeConfig(blackboard=bb)


def _seq(*children) -> ReactiveSequenceNode:
    return ReactiveSequenceNode("seq", children=list(children))


def _fal(*children) -> ReactiveFallbackNode:
    return ReactiveFallbackNode("fal", children=list(children))


# ── ReactiveSequenceNode — basic correctness ──────────────────────────────────

class TestReactiveSequenceBasic:
    def test_all_success_returns_success(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a");    action.set_status(NodeStatus.SUCCESS)
        node   = _seq(cond, action)
        assert node.execute_tick() == NodeStatus.SUCCESS

    def test_condition_failure_returns_failure(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a")
        node   = _seq(cond, action)
        assert node.execute_tick() == NodeStatus.FAILURE

    def test_running_action_returns_running(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _seq(cond, action)
        assert node.execute_tick() == NodeStatus.RUNNING

    def test_action_completes_after_n_ticks(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _seq(cond, action)
        assert node.execute_tick() == NodeStatus.RUNNING
        assert node.execute_tick() == NodeStatus.RUNNING
        assert node.execute_tick() == NodeStatus.SUCCESS

    def test_condition_failure_halts_running_action(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a");    action.set_ticks_to_complete(5)
        node   = _seq(cond, action)
        node.execute_tick()   # RUNNING

        cond.set_bool(False)
        # Next tick: full re-eval (dirty from start), condition fails
        assert node.execute_tick() == NodeStatus.FAILURE
        assert action._status == NodeStatus.IDLE   # halted

    def test_empty_sequence_returns_success(self):
        node = _seq()
        assert node.execute_tick() == NodeStatus.SUCCESS


# ── ReactiveSequenceNode — fast path (no blackboard) ─────────────────────────

class TestReactiveSequenceFastPath:
    """Without a blackboard on condition children, no subscriptions are set up.
    The node falls back to full re-eval every tick (dirty stays True because
    _collect_blackboards() returns nothing).  Validate correct behavior."""

    def test_without_bb_always_full_eval(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _seq(cond, action)

        node.execute_tick()   # tick 1: full eval, action RUNNING
        cond_ticks_after_1 = cond.tick_count_local

        node.execute_tick()   # tick 2: no bb → full eval again
        assert cond.tick_count_local == cond_ticks_after_1 + 1

    def test_no_subscriptions_without_bb(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _seq(cond, action)
        node.execute_tick()
        assert node._bb_subscriptions == []


# ── ReactiveSequenceNode — blackboard-driven dirty flag ───────────────────────

class TestReactiveSequenceDirtyFlag:
    def test_fast_path_rechecks_guards_but_does_not_re_enter_the_action(self):
        """The fast path skips re-entering the RUNNING action, not the guards.

        A guard whose truth comes from outside the blackboard — a sensor, a
        topic, a clock — has to be re-evaluated every tick or the node is not
        reactive at all. Only the action child is protected from re-entry.
        """
        bb     = _bb("rs_fast")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(True)
        action = MockActionNode("a");               action.set_ticks_to_complete(4)
        node   = _seq(cond, action)

        assert node.execute_tick() == NodeStatus.RUNNING
        assert cond.tick_count_local == 1
        assert node._running_child_idx == 1
        assert node._bb_subscriptions  # subscribed

        assert node.execute_tick() == NodeStatus.RUNNING
        assert cond.tick_count_local == 2   # re-checked, no bb write needed

        assert node.execute_tick() == NodeStatus.RUNNING
        assert cond.tick_count_local == 3

        assert node.execute_tick() == NodeStatus.SUCCESS
        assert cond.tick_count_local == 4
        assert action.tick_count_local == 4   # ticked once per tick, never twice

    def test_bb_write_sets_dirty_triggers_full_eval(self):
        bb     = _bb("rs_dirty")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(True)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _seq(cond, action)

        node.execute_tick()   # tick 1: full eval
        assert cond.tick_count_local == 1

        node.execute_tick()   # tick 2: fast path — guard still re-checked
        assert cond.tick_count_local == 2

        # Blackboard write → dirty flag set, forcing a full re-evaluation
        bb.set("obstacle", True)
        assert node._dirty

        node.execute_tick()   # tick 3: dirty → full eval
        assert cond.tick_count_local == 3

    def test_bb_write_with_failed_condition_aborts_action(self):
        bb     = _bb("rs_abort")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(True)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _seq(cond, action)

        node.execute_tick()   # RUNNING, fast path set up

        cond.set_bool(False)
        bb.set("abort_key", 1)   # triggers dirty

        result = node.execute_tick()
        assert result == NodeStatus.FAILURE
        assert action._status == NodeStatus.IDLE

    def test_subscriptions_cleared_after_action_completes(self):
        bb     = _bb("rs_unsub")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(True)
        action = MockActionNode("a");               action.set_ticks_to_complete(2)
        node   = _seq(cond, action)

        node.execute_tick()   # RUNNING — subscribed
        assert node._bb_subscriptions

        node.execute_tick()   # SUCCESS — unsubscribed
        assert node._bb_subscriptions == []

    def test_subscriptions_cleared_on_halt(self):
        bb     = _bb("rs_halt")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(True)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _seq(cond, action)

        node.execute_tick()   # RUNNING — subscribed
        assert node._bb_subscriptions

        node.halt()
        assert node._bb_subscriptions == []
        assert node._running_child_idx is None

    def test_reset_forces_full_eval_next_tick(self):
        bb     = _bb("rs_reset")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(True)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _seq(cond, action)

        node.execute_tick()   # tick 1: full eval
        node.execute_tick()   # tick 2: fast path — guard re-checked

        node.reset_node()
        assert node._dirty

        node.execute_tick()   # tick 3: dirty → full eval
        assert cond.tick_count_local == 3

    def test_multiple_blackboards_all_subscribed(self):
        bb1    = _bb("rs_multi_1")
        bb2    = _bb("rs_multi_2")
        cond1  = MockConditionNode("c1", _cfg(bb1)); cond1.set_bool(True)
        cond2  = MockConditionNode("c2", _cfg(bb2)); cond2.set_bool(True)
        action = MockActionNode("a"); action.set_ticks_to_complete(3)
        node   = _seq(cond1, cond2, action)

        node.execute_tick()   # full eval, subscribed to both bbs
        assert len(node._bb_subscriptions) == 2

        node.execute_tick()   # fast path — both guards re-checked
        assert cond1.tick_count_local == 2
        assert cond2.tick_count_local == 2

        bb1.set("key", "x")   # write to bb1 → dirty
        node.execute_tick()   # full eval
        assert cond1.tick_count_local == 3
        assert cond2.tick_count_local == 3


# ── ReactiveFallbackNode — basic correctness ──────────────────────────────────

class TestReactiveFallbackBasic:
    def test_all_failure_returns_failure(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_status(NodeStatus.FAILURE)
        node   = _fal(cond, action)
        assert node.execute_tick() == NodeStatus.FAILURE

    def test_first_condition_success_returns_success(self):
        cond   = MockConditionNode("c"); cond.set_bool(True)
        action = MockActionNode("a")
        node   = _fal(cond, action)
        assert node.execute_tick() == NodeStatus.SUCCESS

    def test_running_action_returns_running(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _fal(cond, action)
        assert node.execute_tick() == NodeStatus.RUNNING

    def test_condition_success_interrupts_running_action(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(5)
        node   = _fal(cond, action)
        node.execute_tick()   # RUNNING

        cond.set_bool(True)
        result = node.execute_tick()
        assert result == NodeStatus.SUCCESS
        assert action._status == NodeStatus.IDLE

    def test_empty_fallback_returns_failure(self):
        node = _fal()
        assert node.execute_tick() == NodeStatus.FAILURE


# ── ReactiveFallbackNode — no blackboard on children ──────────────────────────

class TestReactiveFallbackFastPath:
    """Mirror of TestReactiveSequenceFastPath.

    Without a blackboard on the condition children there is nothing to subscribe
    to, so `_collect_blackboards()` returns nothing, the dirty flag stays set and
    the node falls back to a full re-evaluation on every tick. ReactiveSequence
    and ReactiveFallback share this logic line for line, so both are covered.
    """

    def test_without_bb_always_full_eval(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _fal(cond, action)

        node.execute_tick()   # tick 1: full eval, action RUNNING
        cond_ticks_after_1 = cond.tick_count_local

        node.execute_tick()   # tick 2: no bb → full eval again
        assert cond.tick_count_local == cond_ticks_after_1 + 1

    def test_no_subscriptions_without_bb(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _fal(cond, action)
        node.execute_tick()
        assert node._bb_subscriptions == []

    def test_guard_flip_still_interrupts_without_bb(self):
        """The reactive guarantee must hold even with no blackboard: a guard that
        starts passing interrupts the running action on the very next tick."""
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(9)
        node   = _fal(cond, action)

        assert node.execute_tick() == NodeStatus.RUNNING
        assert action._status == NodeStatus.RUNNING

        cond.set_bool(True)
        assert node.execute_tick() == NodeStatus.SUCCESS
        assert action._status == NodeStatus.IDLE

    def test_reset_forces_full_eval_next_tick(self):
        """Mirror of the ReactiveSequence reset test.

        `reset_node()` must set the dirty flag so the next tick re-evaluates from
        the first child instead of resuming the fast path.
        """
        bb     = _bb("rf_reset")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(False)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _fal(cond, action)

        node.execute_tick()   # tick 1: full eval
        node.execute_tick()   # tick 2: fast path — guard re-checked

        node.reset_node()
        assert node._dirty

        node.execute_tick()   # tick 3: dirty → full eval
        assert cond.tick_count_local == 3

    def test_running_action_is_not_re_entered_on_each_tick(self):
        """A RUNNING action is resumed, not restarted: its tick count advances by
        exactly one per tick."""
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(5)
        node   = _fal(cond, action)

        node.execute_tick()
        first = action.tick_count_local
        node.execute_tick()
        node.execute_tick()
        assert action.tick_count_local == first + 2


# ── ReactiveFallbackNode — blackboard-driven dirty flag ───────────────────────

class TestReactiveFallbackDirtyFlag:
    def test_fast_path_rechecks_guards_but_does_not_re_enter_the_action(self):
        bb     = _bb("rf_fast")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(False)
        action = MockActionNode("a");               action.set_ticks_to_complete(4)
        action.set_status(NodeStatus.FAILURE)   # fallback needs all children to fail
        node   = _fal(cond, action)

        assert node.execute_tick() == NodeStatus.RUNNING
        assert cond.tick_count_local == 1

        assert node.execute_tick() == NodeStatus.RUNNING
        assert cond.tick_count_local == 2   # guard re-checked every tick

        assert node.execute_tick() == NodeStatus.RUNNING
        assert cond.tick_count_local == 3

        assert node.execute_tick() == NodeStatus.FAILURE
        assert cond.tick_count_local == 4
        assert action.tick_count_local == 4

    def test_bb_write_triggers_full_eval(self):
        bb     = _bb("rf_dirty")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(False)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _fal(cond, action)

        node.execute_tick()   # tick 1: full eval
        node.execute_tick()   # tick 2: fast path — guard still re-checked

        bb.set("status", "changed")
        assert node._dirty

        node.execute_tick()   # tick 3: full eval
        assert cond.tick_count_local == 3

    def test_condition_success_after_bb_write_interrupts_action(self):
        bb     = _bb("rf_interrupt")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(False)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _fal(cond, action)

        node.execute_tick()   # RUNNING

        cond.set_bool(True)
        bb.set("trigger", 1)   # dirty

        result = node.execute_tick()
        assert result == NodeStatus.SUCCESS
        assert action._status == NodeStatus.IDLE

    def test_subscriptions_cleared_on_halt(self):
        bb     = _bb("rf_halt")
        cond   = MockConditionNode("c", _cfg(bb)); cond.set_bool(False)
        action = MockActionNode("a");               action.set_ticks_to_complete(5)
        node   = _fal(cond, action)

        node.execute_tick()
        assert node._bb_subscriptions

        node.halt()
        assert node._bb_subscriptions == []
        assert node._running_child_idx is None

    def test_no_subscriptions_without_bb(self):
        cond   = MockConditionNode("c"); cond.set_bool(False)
        action = MockActionNode("a");    action.set_ticks_to_complete(3)
        node   = _fal(cond, action)
        node.execute_tick()
        assert node._bb_subscriptions == []
