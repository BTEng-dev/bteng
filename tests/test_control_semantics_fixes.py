"""Regression tests for the control/decorator semantics fixes (#4, #5, #6, #7, #11, #17).

Each test names the behaviour it pins down, not the bug number, so the file
stays readable once the bug list is gone.  Where a fix changed user-visible
semantics the test says what the old behaviour was.
"""
from __future__ import annotations

from typing import List, Optional

import pytest

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import NodeConfig, NodeStatus, NodeType
from bteng.core.tree import Tree, TreeMetadata
from bteng.core.tree_builder import TreeBuilder
from bteng.nodes.control.parallel import ParallelNode, ParallelPolicy
from bteng.nodes.control.reactive_fallback import ReactiveFallbackNode
from bteng.nodes.control.reactive_sequence import ReactiveSequenceNode
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.decorators.rate_controller import RateController
from bteng.nodes.decorators.retry import Retry
from bteng.nodes.leaf.action import ActionNode, FunctionAction
from bteng.nodes.leaf.condition import FunctionCondition
from bteng.nodes.leaf.stateful_action import StatefulActionNode


# ── Helpers ───────────────────────────────────────────────────────────────────

class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def monotonic(self) -> float:
        return self.t


class Recorder(ActionNode):
    """Action returning a fixed status, recording every tick."""

    def __init__(self, name: str, status: NodeStatus, log: List[str]) -> None:
        super().__init__(name)
        self._result = status
        self._log = log

    def tick(self) -> NodeStatus:
        self._log.append(self.name)
        return self._result


class Scripted(ActionNode):
    """Action returning statuses from a script, then repeating the last one."""

    def __init__(self, name: str, script: List[NodeStatus]) -> None:
        super().__init__(name)
        self._script = list(script)
        self._i = 0
        self.ticks = 0

    def tick(self) -> NodeStatus:
        self.ticks += 1
        status = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return status


def _fixed(status: NodeStatus, name: str = "a") -> FunctionAction:
    return FunctionAction(name, lambda _: status)


def _tick_n(node, n: int) -> List[NodeStatus]:
    return [node.execute_tick() for _ in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# #4 — thresholds clamped to the live child count (no more permanent livelock)
# ─────────────────────────────────────────────────────────────────────────────

class TestParallelThresholdClamping:
    def test_success_threshold_above_child_count_completes(self):
        """success_threshold=3 over 2 children used to stay RUNNING forever."""
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.SUCCESS, "B")],
                         success_threshold=3, failure_threshold=99)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_failure_threshold_above_child_count_completes(self):
        """failure_threshold=3 over 2 failing children used to stay RUNNING forever."""
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"),
                                        _fixed(NodeStatus.FAILURE, "B")],
                         success_threshold=2, failure_threshold=3)
        assert p.execute_tick() == NodeStatus.FAILURE

    def test_no_livelock_when_neither_threshold_can_fire(self):
        """1 SUCCESS + 1 FAILURE with success=2, failure=2 has no reachable branch.

        Completed children are never re-ticked, so RUNNING here was terminal.
        The node now decides FAILURE once every child is terminal.
        """
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.FAILURE, "B")],
                         success_threshold=2, failure_threshold=2)
        assert p.execute_tick() == NodeStatus.FAILURE

    def test_livelocked_node_does_not_stop_ticking_children(self):
        """Regression guard: the old shape burned zero CPU and never progressed."""
        log: List[str] = []
        p = ParallelNode("p", children=[Recorder("A", NodeStatus.SUCCESS, log),
                                        Recorder("B", NodeStatus.SUCCESS, log)],
                         success_threshold=10)
        assert p.execute_tick() == NodeStatus.SUCCESS
        assert log == ["A", "B"]

    def test_zero_or_negative_failure_threshold_is_clamped_to_one(self):
        """failure_threshold=0 would otherwise make every parallel fail instantly."""
        p = ParallelNode("p", children=[_fixed(NodeStatus.RUNNING, "A")],
                         failure_threshold=0)
        assert p.execute_tick() == NodeStatus.RUNNING

    def test_threshold_still_tracks_children_added_after_init(self):
        p = ParallelNode("p", children=[], policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        p._children.append(_fixed(NodeStatus.SUCCESS, "A"))
        assert p.execute_tick() == NodeStatus.SUCCESS
        p._children.append(_fixed(NodeStatus.RUNNING, "B"))
        assert p.execute_tick() == NodeStatus.RUNNING


class TestParallelEmptyChildren:
    """No children → vacuous SUCCESS under every policy, never RUNNING."""

    @pytest.mark.parametrize("policy", [
        None,
        ParallelPolicy.REQUIRE_ALL_SUCCESS,
        ParallelPolicy.REQUIRE_ONE_SUCCESS,
        ParallelPolicy.REQUIRE_ALL_COMPLETE,
    ])
    def test_empty_parallel_succeeds(self, policy):
        p = ParallelNode("p", children=[], policy=policy)
        assert p.execute_tick() == NodeStatus.SUCCESS
        # And it stays decided — REQUIRE_ONE_SUCCESS used to return RUNNING forever.
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_empty_parallel_matches_empty_sequence(self):
        assert (ParallelNode("p", []).execute_tick()
                == SequenceNode("s", []).execute_tick()
                == NodeStatus.SUCCESS)


# ─────────────────────────────────────────────────────────────────────────────
# #6 — success_threshold <= 0 means "all children"
# ─────────────────────────────────────────────────────────────────────────────

class TestSuccessThresholdZeroMeansAll:
    def test_zero_threshold_respects_child_results(self):
        """success_threshold=0 used to return SUCCESS while discarding two FAILUREs."""
        log: List[str] = []
        p = ParallelNode("p", children=[Recorder("A", NodeStatus.FAILURE, log),
                                        Recorder("B", NodeStatus.FAILURE, log)],
                         success_threshold=0)
        assert p.execute_tick() == NodeStatus.FAILURE
        assert log == ["A", "B"]

    def test_zero_threshold_needs_every_child_to_succeed(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         success_threshold=0)
        assert p.execute_tick() == NodeStatus.RUNNING

    def test_zero_threshold_succeeds_when_all_succeed(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.SUCCESS, "B")],
                         success_threshold=0)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_zero_threshold_via_tree_builder(self):
        log: List[str] = []
        b = TreeBuilder()
        b.parallel("p", success_threshold=0)
        b.action("A", lambda: (log.append("A"), NodeStatus.FAILURE)[1])
        b.end()
        assert b.build().tick_once() == NodeStatus.FAILURE
        assert log == ["A"]

    def test_zero_threshold_via_xml(self):
        from bteng.factory.factory import NodeFactory
        from bteng.xml_parser.parser import XMLTreeParser

        class Failing(ActionNode):
            def tick(self) -> NodeStatus:
                return NodeStatus.FAILURE

        xml = """
        <root main_tree_to_execute="T">
          <Tree ID="T">
            <Parallel success_threshold="0">
              <Failing/>
              <Failing/>
            </Parallel>
          </Tree>
        </root>
        """
        factory = NodeFactory()
        factory.register(Failing, "Failing")
        root = XMLTreeParser(factory).parse_string(xml)
        node = root.root if isinstance(root, Tree) else root
        assert node.execute_tick() == NodeStatus.FAILURE


# ─────────────────────────────────────────────────────────────────────────────
# #5 — policies derive BOTH thresholds
# ─────────────────────────────────────────────────────────────────────────────

class TestRequireOneSuccess:
    def test_one_failing_child_does_not_abort_the_node(self):
        """Used to return FAILURE on the first failing child."""
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        assert p.execute_tick() == NodeStatus.RUNNING

    def test_later_child_success_still_wins_after_a_failure(self):
        b = Scripted("B", [NodeStatus.RUNNING, NodeStatus.RUNNING, NodeStatus.SUCCESS])
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"), b],
                         policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        assert _tick_n(p, 3) == [NodeStatus.RUNNING, NodeStatus.RUNNING,
                                 NodeStatus.SUCCESS]

    def test_failing_child_is_not_halted_while_others_run(self):
        """The still-running sibling keeps its RUNNING state across ticks."""
        b = Scripted("B", [NodeStatus.RUNNING])
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"), b],
                         policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        p.execute_tick()
        assert b.status == NodeStatus.RUNNING
        p.execute_tick()
        assert b.ticks == 2

    def test_fails_only_when_every_child_failed(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"),
                                        _fixed(NodeStatus.FAILURE, "B")],
                         policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        assert p.execute_tick() == NodeStatus.FAILURE

    def test_first_success_wins_immediately(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_success_threshold_is_ignored(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         success_threshold=2,
                         policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        assert p.execute_tick() == NodeStatus.SUCCESS


class TestRequireAllComplete:
    def test_waits_for_every_child_before_deciding(self):
        """A failing child used to abort the node and halt the running sibling."""
        b = Scripted("B", [NodeStatus.RUNNING, NodeStatus.RUNNING, NodeStatus.SUCCESS])
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"), b],
                         policy=ParallelPolicy.REQUIRE_ALL_COMPLETE)
        assert p.execute_tick() == NodeStatus.RUNNING
        assert b.status == NodeStatus.RUNNING       # not halted
        assert p.execute_tick() == NodeStatus.RUNNING

    def test_default_threshold_requires_all_to_succeed(self):
        b = Scripted("B", [NodeStatus.RUNNING, NodeStatus.SUCCESS])
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"), b],
                         policy=ParallelPolicy.REQUIRE_ALL_COMPLETE)
        assert p.execute_tick() == NodeStatus.RUNNING
        # every child terminal now: 1 success of 2 required → FAILURE
        assert p.execute_tick() == NodeStatus.FAILURE

    def test_success_threshold_is_honoured(self):
        """success_threshold used to be overwritten with the child count."""
        b = Scripted("B", [NodeStatus.RUNNING, NodeStatus.SUCCESS])
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"), b],
                         success_threshold=1,
                         policy=ParallelPolicy.REQUIRE_ALL_COMPLETE)
        assert p.execute_tick() == NodeStatus.RUNNING
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_all_success_succeeds(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.SUCCESS, "B")],
                         policy=ParallelPolicy.REQUIRE_ALL_COMPLETE)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_no_early_success_either(self):
        """Even with the threshold met, still-running children are awaited."""
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         success_threshold=1,
                         policy=ParallelPolicy.REQUIRE_ALL_COMPLETE)
        assert p.execute_tick() == NodeStatus.RUNNING


class TestRequireAllSuccess:
    def test_first_failure_aborts(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.FAILURE, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        assert p.execute_tick() == NodeStatus.FAILURE

    def test_all_success_succeeds(self):
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.SUCCESS, "B")],
                         policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        assert p.execute_tick() == NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# success_threshold is a real input port
# ─────────────────────────────────────────────────────────────────────────────

class TestSuccessThresholdPort:
    def _parallel_with_bb(self, bb: Blackboard) -> ParallelNode:
        cfg = NodeConfig(blackboard=bb,
                         input_ports={"success_threshold": "max_ok"})
        return ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                           _fixed(NodeStatus.RUNNING, "B")],
                            config=cfg)

    def test_blackboard_mapped_threshold_takes_effect(self):
        """get_input() was never called, so a remapped port was silently ignored."""
        bb = Blackboard(scope_name="p_port")
        bb.set("max_ok", 1)
        assert self._parallel_with_bb(bb).execute_tick() == NodeStatus.SUCCESS

    def test_blackboard_mapped_threshold_is_re_read_each_tick(self):
        bb = Blackboard(scope_name="p_port2")
        bb.set("max_ok", 2)
        p = self._parallel_with_bb(bb)
        assert p.execute_tick() == NodeStatus.RUNNING   # needs 2 successes
        bb.set("max_ok", 1)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_string_threshold_from_blackboard_is_coerced(self):
        bb = Blackboard(scope_name="p_port3")
        bb.set("max_ok", "1")
        assert self._parallel_with_bb(bb).execute_tick() == NodeStatus.SUCCESS

    def test_unparsable_threshold_falls_back_to_the_constructor_value(self):
        bb = Blackboard(scope_name="p_port4")
        bb.set("max_ok", "not-a-number")
        # ctor default -1 → "all children" → 1 SUCCESS of 2 is not enough
        assert self._parallel_with_bb(bb).execute_tick() == NodeStatus.RUNNING

    def test_static_param_threshold_takes_effect(self):
        cfg = NodeConfig(params={"success_threshold": 1})
        p = ParallelNode("p", children=[_fixed(NodeStatus.SUCCESS, "A"),
                                        _fixed(NodeStatus.RUNNING, "B")],
                         config=cfg)
        assert p.execute_tick() == NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# #7 — RateController state cleared on reset, not only on halt
# ─────────────────────────────────────────────────────────────────────────────

class TestRateControllerReset:
    def test_on_reset_clears_rate_state(self):
        clk = FakeClock()
        rc = RateController("rc", child=_fixed(NodeStatus.SUCCESS), hz=1.0, clock=clk)
        rc.execute_tick()
        assert rc._last_tick is not None
        rc._on_reset()
        assert rc._last_tick is None
        assert rc._last_status == NodeStatus.IDLE

    def test_parallel_second_activation_ticks_the_child(self):
        """The child was skipped on re-activation and a stale SUCCESS replayed."""
        clk = FakeClock()
        log: List[str] = []
        rc = RateController("rc", child=Recorder("w", NodeStatus.SUCCESS, log),
                            hz=1.0, clock=clk)
        p = ParallelNode("par", children=[rc], success_threshold=1)

        assert p.execute_tick() == NodeStatus.SUCCESS
        assert log == ["w"]
        assert rc._last_tick is None, "Parallel must reset the RateController"

        # No time has passed: a fresh activation must still do the work.
        assert p.execute_tick() == NodeStatus.SUCCESS
        assert log == ["w", "w"]

    def test_rate_limiting_still_applies_within_one_activation(self):
        clk = FakeClock()
        log: List[str] = []
        rc = RateController("rc", child=Recorder("w", NodeStatus.RUNNING, log),
                            hz=1.0, clock=clk)
        assert rc.execute_tick() == NodeStatus.RUNNING
        assert rc.execute_tick() == NodeStatus.RUNNING
        assert log == ["w"], "second tick must be rate-limited, not re-run"
        clk.t = 1.0
        rc.execute_tick()
        assert log == ["w", "w"]

    def test_rate_controller_never_replays_idle(self):
        clk = FakeClock()
        rc = RateController("rc", child=_fixed(NodeStatus.RUNNING), hz=1.0, clock=clk)
        p = ParallelNode("par", children=[rc, _fixed(NodeStatus.RUNNING, "other")],
                         success_threshold=2)
        p.execute_tick()
        p.execute_tick()
        assert rc.status != NodeStatus.IDLE or rc._last_status != NodeStatus.IDLE


class TestParallelResetsChildSubtrees:
    def test_grandchild_sequence_cursor_is_reset(self):
        inner = SequenceNode("seq", [_fixed(NodeStatus.SUCCESS, "a"),
                                     _fixed(NodeStatus.SUCCESS, "b")])
        outer = SequenceNode("outer", [inner])
        p = ParallelNode("par", children=[outer], success_threshold=1)
        assert p.execute_tick() == NodeStatus.SUCCESS
        assert inner._current_idx == 0
        assert inner.status == NodeStatus.IDLE

    def test_completed_stateful_child_is_not_told_it_was_halted(self):
        """reset_node() would deliver on_halted() to an action that SUCCEEDED."""
        events: List[str] = []

        class Sf(StatefulActionNode):
            def on_start(self) -> NodeStatus:
                events.append("start")
                return NodeStatus.SUCCESS

            def on_running(self) -> NodeStatus:  # pragma: no cover
                return NodeStatus.SUCCESS

            def on_halted(self) -> None:
                events.append("halted")

        p = ParallelNode("par", children=[Sf("sf")], success_threshold=1)
        assert p.execute_tick() == NodeStatus.SUCCESS
        assert events == ["start"], f"unexpected lifecycle events: {events}"

    def test_running_child_subtree_is_still_halted_properly(self):
        events: List[str] = []

        class Sf(StatefulActionNode):
            def on_start(self) -> NodeStatus:
                return NodeStatus.RUNNING

            def on_running(self) -> NodeStatus:
                return NodeStatus.RUNNING

            def on_halted(self) -> None:
                events.append("halted")

        p = ParallelNode("par", children=[_fixed(NodeStatus.FAILURE, "A"), Sf("sf")],
                         failure_threshold=1)
        assert p.execute_tick() == NodeStatus.FAILURE
        assert events == ["halted"]


# ─────────────────────────────────────────────────────────────────────────────
# #17 — Retry(max_attempts <= 0)
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryAttemptBudget:
    @pytest.mark.parametrize("max_attempts", [0, -1, -5])
    def test_no_budget_means_no_child_tick(self, max_attempts):
        log: List[str] = []
        r = Retry("r", child=Recorder("c", NodeStatus.FAILURE, log),
                  max_attempts=max_attempts)
        assert r.execute_tick() == NodeStatus.FAILURE
        assert log == []

    def test_zero_budget_stays_failure_when_re_ticked(self):
        log: List[str] = []
        r = Retry("r", child=Recorder("c", NodeStatus.SUCCESS, log), max_attempts=0)
        assert _tick_n(r, 3) == [NodeStatus.FAILURE] * 3
        assert log == []

    def test_one_attempt_runs_the_child_once(self):
        log: List[str] = []
        r = Retry("r", child=Recorder("c", NodeStatus.FAILURE, log), max_attempts=1)
        assert r.execute_tick() == NodeStatus.FAILURE
        assert log == ["c"]

    def test_three_attempts_runs_exactly_three_times(self):
        log: List[str] = []
        r = Retry("r", child=Recorder("c", NodeStatus.FAILURE, log), max_attempts=3)
        assert _tick_n(r, 3) == [NodeStatus.RUNNING, NodeStatus.RUNNING,
                                 NodeStatus.FAILURE]
        assert log == ["c", "c", "c"]

    def test_success_short_circuits(self):
        log: List[str] = []
        r = Retry("r", child=Recorder("c", NodeStatus.SUCCESS, log), max_attempts=3)
        assert r.execute_tick() == NodeStatus.SUCCESS
        assert log == ["c"]


# ─────────────────────────────────────────────────────────────────────────────
# #11 — the reactive guarantee no longer depends on the blackboard
# ─────────────────────────────────────────────────────────────────────────────

class _Sensor:
    """State that lives outside any blackboard (stands in for a ROS topic)."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.reads = 0

    def read(self) -> bool:
        self.reads += 1
        return self.ok


def _cond(sensor: _Sensor, cfg: Optional[NodeConfig] = None) -> FunctionCondition:
    return FunctionCondition("guard", lambda _: sensor.read(), config=cfg)


class TestReactiveSequenceAlwaysRechecksConditions:
    def test_non_blackboard_condition_interrupts_a_running_action(self):
        """The whole point of the node: the guard must be re-read every tick.

        With a Blackboard present the fast path used to skip the condition, so
        a sensor going bad never interrupted the action.
        """
        bb = Blackboard(scope_name="rs_sensor")
        cfg = NodeConfig(blackboard=bb)
        sensor = _Sensor(ok=True)
        action = Scripted("act", [NodeStatus.RUNNING])
        node = ReactiveSequenceNode("rs", [_cond(sensor, cfg), action], config=cfg)

        assert node.execute_tick() == NodeStatus.RUNNING
        assert node._bb_subscriptions, "fast path must be armed for a real test"
        assert node.execute_tick() == NodeStatus.RUNNING

        sensor.ok = False
        assert node.execute_tick() == NodeStatus.FAILURE
        assert action.status == NodeStatus.IDLE, "running action must be halted"

    def test_condition_is_ticked_on_every_tick(self):
        bb = Blackboard(scope_name="rs_count")
        cfg = NodeConfig(blackboard=bb)
        sensor = _Sensor(ok=True)
        node = ReactiveSequenceNode(
            "rs", [_cond(sensor, cfg), Scripted("act", [NodeStatus.RUNNING])], config=cfg)
        _tick_n(node, 4)
        assert sensor.reads == 4

    def test_running_action_is_not_re_entered_from_the_start(self):
        """The dirty flag still protects *actions* preceding the running child."""
        bb = Blackboard(scope_name="rs_actions")
        cfg = NodeConfig(blackboard=bb)
        done = Scripted("done", [NodeStatus.SUCCESS])
        running = Scripted("running", [NodeStatus.RUNNING])
        node = ReactiveSequenceNode("rs", [_cond(_Sensor(), cfg), done, running],
                                    config=cfg)
        _tick_n(node, 3)
        assert done.ticks == 1, "completed action must not be re-entered"
        assert running.ticks == 3

    def test_blackboard_write_still_forces_a_full_re_evaluation(self):
        bb = Blackboard(scope_name="rs_dirty2")
        cfg = NodeConfig(blackboard=bb)
        done = Scripted("done", [NodeStatus.SUCCESS])
        node = ReactiveSequenceNode(
            "rs", [_cond(_Sensor(), cfg), done, Scripted("act", [NodeStatus.RUNNING])],
            config=cfg)
        node.execute_tick()
        node.execute_tick()
        assert done.ticks == 1
        bb.set("anything", 1)
        node.execute_tick()
        assert done.ticks == 2

    def test_stale_running_cursor_falls_back_to_full_eval(self):
        bb = Blackboard(scope_name="rs_stale")
        cfg = NodeConfig(blackboard=bb)
        node = ReactiveSequenceNode(
            "rs", [_cond(_Sensor(), cfg), Scripted("act", [NodeStatus.RUNNING])],
            config=cfg)
        node.execute_tick()
        node._children.pop()                 # simulate a runtime REMOVE_CHILD
        node._dirty = False
        assert node._running_child_idx == 1   # now out of range
        assert node.execute_tick() == NodeStatus.SUCCESS  # no IndexError


class TestReactiveFallbackAlwaysRechecksConditions:
    def test_non_blackboard_condition_preempts_a_running_action(self):
        bb = Blackboard(scope_name="rf_sensor")
        cfg = NodeConfig(blackboard=bb)
        sensor = _Sensor(ok=False)
        action = Scripted("act", [NodeStatus.RUNNING])
        node = ReactiveFallbackNode("rf", [_cond(sensor, cfg), action], config=cfg)

        assert node.execute_tick() == NodeStatus.RUNNING
        assert node._bb_subscriptions
        assert node.execute_tick() == NodeStatus.RUNNING

        sensor.ok = True
        assert node.execute_tick() == NodeStatus.SUCCESS
        assert action.status == NodeStatus.IDLE

    def test_condition_is_ticked_on_every_tick(self):
        bb = Blackboard(scope_name="rf_count")
        cfg = NodeConfig(blackboard=bb)
        sensor = _Sensor(ok=False)
        node = ReactiveFallbackNode(
            "rf", [_cond(sensor, cfg), Scripted("act", [NodeStatus.RUNNING])], config=cfg)
        _tick_n(node, 4)
        assert sensor.reads == 4

    def test_failed_action_is_not_re_entered(self):
        bb = Blackboard(scope_name="rf_actions")
        cfg = NodeConfig(blackboard=bb)
        failed = Scripted("failed", [NodeStatus.FAILURE])
        running = Scripted("running", [NodeStatus.RUNNING])
        node = ReactiveFallbackNode("rf", [_cond(_Sensor(False), cfg), failed, running],
                                    config=cfg)
        _tick_n(node, 3)
        assert failed.ticks == 1
        assert running.ticks == 3

    def test_stale_running_cursor_falls_back_to_full_eval(self):
        bb = Blackboard(scope_name="rf_stale")
        cfg = NodeConfig(blackboard=bb)
        node = ReactiveFallbackNode(
            "rf", [_cond(_Sensor(False), cfg), Scripted("act", [NodeStatus.RUNNING])],
            config=cfg)
        node.execute_tick()
        node._children.pop()
        node._dirty = False
        assert node._running_child_idx == 1
        assert node.execute_tick() == NodeStatus.FAILURE  # no IndexError


class TestConditionGuardDetection:
    """A guard wrapped in a decorator/control is still a guard."""

    def test_inverter_wrapped_condition_is_re_evaluated(self):
        from bteng.nodes.decorators.inverter import Inverter

        bb = Blackboard(scope_name="rs_inv")
        cfg = NodeConfig(blackboard=bb)
        sensor = _Sensor(ok=False)          # Inverter turns FAILURE into SUCCESS
        action = Scripted("act", [NodeStatus.RUNNING])
        node = ReactiveSequenceNode("rs", [Inverter("inv", _cond(sensor, cfg)), action],
                                    config=cfg)

        assert node.execute_tick() == NodeStatus.RUNNING
        assert node._bb_subscriptions
        assert node.execute_tick() == NodeStatus.RUNNING
        assert sensor.reads == 2

        sensor.ok = True                     # inverted → FAILURE
        assert node.execute_tick() == NodeStatus.FAILURE
        assert action.status == NodeStatus.IDLE

    def test_sequence_of_conditions_is_a_guard(self):
        bb = Blackboard(scope_name="rs_seqguard")
        cfg = NodeConfig(blackboard=bb)
        s1, s2 = _Sensor(True), _Sensor(True)
        guard = SequenceNode("guard", [_cond(s1, cfg), _cond(s2, cfg)])
        node = ReactiveSequenceNode("rs", [guard, Scripted("act", [NodeStatus.RUNNING])],
                                    config=cfg)
        _tick_n(node, 3)
        assert s1.reads == 3 and s2.reads == 3

    def test_a_wrapper_containing_an_action_is_not_a_guard(self):
        """Actions stay protected by the dirty flag — they are not re-entered."""
        from bteng.nodes.decorators.inverter import Inverter

        bb = Blackboard(scope_name="rs_notguard")
        cfg = NodeConfig(blackboard=bb)
        inner = Scripted("inner", [NodeStatus.FAILURE])   # Inverter → SUCCESS
        node = ReactiveSequenceNode(
            "rs", [_cond(_Sensor(), cfg), Inverter("inv", inner),
                   Scripted("act", [NodeStatus.RUNNING])],
            config=cfg)
        _tick_n(node, 3)
        assert inner.ticks == 1

    def test_reactive_fallback_re_evaluates_a_wrapped_guard(self):
        from bteng.nodes.decorators.inverter import Inverter

        bb = Blackboard(scope_name="rf_inv")
        cfg = NodeConfig(blackboard=bb)
        sensor = _Sensor(ok=True)            # Inverter → FAILURE
        action = Scripted("act", [NodeStatus.RUNNING])
        node = ReactiveFallbackNode("rf", [Inverter("inv", _cond(sensor, cfg)), action],
                                    config=cfg)
        assert node.execute_tick() == NodeStatus.RUNNING
        assert node.execute_tick() == NodeStatus.RUNNING
        sensor.ok = False                    # inverted → SUCCESS
        assert node.execute_tick() == NodeStatus.SUCCESS
        assert action.status == NodeStatus.IDLE


class TestReactiveSemanticsIndependentOfBlackboard:
    """A Blackboard must not change which semantics you get."""

    @staticmethod
    def _run(with_bb: bool) -> int:
        cfg = NodeConfig(blackboard=Blackboard(scope_name="rs_iso")) if with_bb else None
        sensor = _Sensor(ok=True)
        node = ReactiveSequenceNode(
            "rs", [_cond(sensor, cfg), Scripted("act", [NodeStatus.RUNNING])], config=cfg)
        _tick_n(node, 3)
        return sensor.reads

    def test_condition_read_count_is_the_same_with_and_without_a_blackboard(self):
        assert self._run(with_bb=True) == self._run(with_bb=False) == 3
