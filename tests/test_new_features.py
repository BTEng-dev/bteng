"""Tests for features added post-initial-release.

Covers:
  - feedback_message (set_feedback_message / backward-compat aliases)
  - setup() / shutdown() lifecycle hooks
  - Built-in leaf nodes (AlwaysSuccess/Failure/Running, SetBlackboard, CheckBlackboard)
  - tip() traversal on all node types
  - ascii_tree() / print_tree() renderer
  - Clock protocol + WallClock
  - Timeout with injected clock
  - RateController with injected clock
"""
from __future__ import annotations

import io
import sys
from typing import Optional

import pytest

from bteng.blackboard.blackboard import Blackboard
from bteng.concurrency.clock import Clock, WallClock
from bteng.core.executor import ExecutorConfig, TreeExecutor
from bteng.core.node import (
    ControlNode, DecoratorNode, LeafNode,
    NodeConfig, NodeStatus, TreeNode,
)
from bteng.core.tree import Tree, TreeMetadata
from bteng.introspection.inspector import Inspector
from bteng.introspection.renderer import ascii_tree, print_tree
from bteng.nodes.control.fallback import FallbackNode
from bteng.nodes.control.parallel import ParallelNode, ParallelPolicy
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.decorators.inverter import Inverter
from bteng.nodes.decorators.rate_controller import RateController
from bteng.nodes.decorators.timeout import Timeout
from bteng.nodes.leaf.action import ActionNode, FunctionAction
from bteng.nodes.leaf.builtins import (
    AlwaysFailure, AlwaysRunning, AlwaysSuccess,
    CheckBlackboard, SetBlackboard,
)
from bteng.nodes.leaf.condition import FunctionCondition
from bteng.testing.mock_nodes import MockActionNode


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _Running(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.RUNNING

class _Success(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS

class _Failure(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.FAILURE


class FakeClock:
    """Deterministic clock for testing time-dependent decorators."""
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _bb_config(bb: Blackboard, **ports) -> NodeConfig:
    """Build a NodeConfig with the given blackboard and static params."""
    return NodeConfig(blackboard=bb, params=ports)


def _make_tree(root: TreeNode) -> Tree:
    return Tree(TreeMetadata(id="test"), root)


# ─────────────────────────────────────────────────────────────────────────────
# feedback_message
# ─────────────────────────────────────────────────────────────────────────────

class TestFeedbackMessage:
    def test_set_and_get(self):
        node = _Success("n")
        node.set_feedback_message("all good")
        assert node.feedback_message == "all good"

    def test_empty_by_default(self):
        assert _Success("n").feedback_message == ""

    def test_set_failure_reason_alias(self):
        node = _Success("n")
        node.set_failure_reason("something broke")
        assert node.feedback_message == "something broke"

    def test_failure_reason_property_alias(self):
        node = _Success("n")
        node.set_feedback_message("msg")
        assert node.failure_reason == "msg"

    def test_aliases_share_same_field(self):
        node = _Success("n")
        node.set_failure_reason("via alias")
        assert node.feedback_message == "via alias"
        node.set_feedback_message("via new")
        assert node.failure_reason == "via new"

    def test_message_persists_across_ticks(self):
        class _MsgAction(ActionNode):
            def tick(self):
                self.set_feedback_message("working")
                return NodeStatus.RUNNING

        node = _MsgAction("n")
        node.execute_tick()
        node.execute_tick()
        assert node.feedback_message == "working"

    def test_message_cleared_on_reset_node(self):
        node = _Success("n")
        node.set_feedback_message("stale")
        node.reset_node()
        assert node.feedback_message == ""

    def test_message_not_cleared_on_halt(self):
        """halt() preserves the message for post-mortem inspection."""
        class _RunningMsg(ActionNode):
            def tick(self):
                self.set_feedback_message("was running")
                return NodeStatus.RUNNING

        node = _RunningMsg("n")
        node.execute_tick()
        node.halt()
        assert node.feedback_message == "was running"

    def test_inspector_receives_feedback_message(self):
        class _MsgNode(ActionNode):
            def tick(self):
                self.set_feedback_message("hello inspector")
                return NodeStatus.SUCCESS

        inspector = Inspector()
        node = _MsgNode("n")
        node._inspector = inspector
        node.execute_tick()

        records = inspector.execution_history()
        assert records[-1].feedback_message == "hello inspector"

    def test_inspector_failure_reason_property_alias(self):
        """NodeExecutionRecord.failure_reason is a backward-compat alias."""
        class _FailNode(ActionNode):
            def tick(self):
                self.set_failure_reason("bad state")
                return NodeStatus.FAILURE

        inspector = Inspector()
        node = _FailNode("n")
        node._inspector = inspector
        node.execute_tick()

        rec = inspector.execution_history()[-1]
        assert rec.feedback_message == "bad state"
        assert rec.failure_reason == "bad state"


# ─────────────────────────────────────────────────────────────────────────────
# setup() / shutdown()
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupShutdown:

    def _executor(self) -> TreeExecutor:
        return TreeExecutor(ExecutorConfig(tick_interval=0.0, halt_on_completion=True))

    def test_setup_called_before_first_tick(self):
        calls = []

        class _Node(_Success):
            def setup(self): calls.append("setup")

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("n")))
        assert calls == []
        ex.tick_once()
        assert calls == ["setup"]

    def test_setup_called_only_once(self):
        calls = []

        class _Node(_Running):
            def setup(self): calls.append("setup")

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("n")))
        ex.tick_once()
        ex.tick_once()
        ex.tick_once()
        assert calls == ["setup"]

    def test_shutdown_called_by_executor_shutdown(self):
        calls = []

        class _Node(_Running):
            def shutdown(self): calls.append("shutdown")

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("n")))
        ex.tick_once()
        ex.shutdown()
        assert calls == ["shutdown"]

    def test_shutdown_idempotent(self):
        calls = []

        class _Node(_Running):
            def shutdown(self): calls.append("shutdown")

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("n")))
        ex.tick_once()
        ex.shutdown()
        ex.shutdown()  # second call must be no-op
        assert calls == ["shutdown"]

    def test_new_set_tree_resets_setup_flag(self):
        calls = []

        class _Node(_Success):
            def __init__(self, label):
                super().__init__(label)
                self._label = label
            def setup(self): calls.append(self._label)

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("first")))
        ex.tick_once()
        ex.set_tree(_make_tree(_Node("second")))
        ex.tick_once()
        assert calls == ["first", "second"]

    def test_setup_called_on_all_nodes(self):
        calls = []

        class _Trackable(_Success):
            def __init__(self, label):
                super().__init__(label)
                self._label = label
            def setup(self): calls.append(self._label)

        root = SequenceNode("seq", children=[
            _Trackable("a"),
            _Trackable("b"),
            _Trackable("c"),
        ])
        ex = self._executor()
        ex.set_tree(_make_tree(root))
        ex.tick_once()
        assert sorted(calls) == ["a", "b", "c"]

    def test_shutdown_called_on_all_nodes(self):
        calls = []

        class _Trackable(_Running):
            def __init__(self, label):
                super().__init__(label)
                self._label = label
            def shutdown(self): calls.append(self._label)

        root = SequenceNode("seq", children=[
            _Trackable("a"),
            _Trackable("b"),
        ])
        ex = self._executor()
        ex.set_tree(_make_tree(root))
        ex.tick_once()
        ex.shutdown()
        assert sorted(calls) == ["a", "b"]

    def test_setup_exception_caught_not_raised(self, capsys):
        class _BadSetup(_Running):
            def setup(self): raise RuntimeError("setup boom")

        ex = self._executor()
        ex.set_tree(_make_tree(_BadSetup("n")))
        ex.tick_once()  # must not raise
        assert "setup boom" in capsys.readouterr().err

    def test_shutdown_exception_caught_not_raised(self, capsys):
        class _BadShutdown(_Running):
            def shutdown(self): raise RuntimeError("shutdown boom")

        ex = self._executor()
        ex.set_tree(_make_tree(_BadShutdown("n")))
        ex.tick_once()
        ex.shutdown()  # must not raise
        assert "shutdown boom" in capsys.readouterr().err

    def test_stop_event_loop_triggers_shutdown(self):
        import time as _time
        calls = []

        class _Node(_Running):
            def shutdown(self): calls.append("shutdown")

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("n")))
        ex.start_event_loop()
        _time.sleep(0.05)
        ex.stop_event_loop()
        assert calls == ["shutdown"]

    def test_event_loop_completion_triggers_shutdown(self):
        import time as _time
        calls = []

        class _Node(_Success):
            def shutdown(self): calls.append("shutdown")

        ex = self._executor()
        ex.set_tree(_make_tree(_Node("n")))
        ex.start_event_loop()
        _time.sleep(0.1)
        assert calls == ["shutdown"]


# ─────────────────────────────────────────────────────────────────────────────
# Built-in leaf nodes
# ─────────────────────────────────────────────────────────────────────────────

class TestAlwaysNodes:
    def test_always_success(self):
        assert AlwaysSuccess("n").execute_tick() == NodeStatus.SUCCESS

    def test_always_failure(self):
        assert AlwaysFailure("n").execute_tick() == NodeStatus.FAILURE

    def test_always_running(self):
        assert AlwaysRunning("n").execute_tick() == NodeStatus.RUNNING

    def test_always_success_repeated(self):
        n = AlwaysSuccess("n")
        for _ in range(5):
            assert n.execute_tick() == NodeStatus.SUCCESS

    def test_always_failure_repeated(self):
        n = AlwaysFailure("n")
        for _ in range(5):
            assert n.execute_tick() == NodeStatus.FAILURE

    def test_always_running_repeated(self):
        n = AlwaysRunning("n")
        for _ in range(5):
            assert n.execute_tick() == NodeStatus.RUNNING


class TestSetBlackboard:
    def _node(self, key="k", value="v") -> SetBlackboard:
        bb = Blackboard.create(f"sb_{key}_{id(self)}")
        config = NodeConfig(blackboard=bb, params={"key": key, "value": value})
        return SetBlackboard("Set", config=config)

    def test_writes_value_to_blackboard(self):
        bb = Blackboard.create("sb_test_write")
        config = NodeConfig(blackboard=bb, params={"key": "x", "value": 42})
        node = SetBlackboard("Set", config=config)
        status = node.execute_tick()
        assert status == NodeStatus.SUCCESS
        assert bb.get("x") == 42

    def test_returns_success(self):
        assert self._node().execute_tick() == NodeStatus.SUCCESS

    def test_sets_feedback_message_on_success(self):
        node = self._node(key="foo", value="bar")
        node.execute_tick()
        assert "foo" in node.feedback_message

    def test_failure_when_key_port_missing(self):
        bb = Blackboard.create("sb_no_key")
        config = NodeConfig(blackboard=bb, params={"value": 1})
        node = SetBlackboard("Set", config=config)
        assert node.execute_tick() == NodeStatus.FAILURE
        assert node.feedback_message != ""

    def test_failure_when_no_blackboard(self):
        config = NodeConfig(params={"key": "k", "value": 1})
        node = SetBlackboard("Set", config=config)
        assert node.execute_tick() == NodeStatus.FAILURE

    def test_writes_none_value(self):
        bb = Blackboard.create("sb_none")
        config = NodeConfig(blackboard=bb, params={"key": "k", "value": None})
        node = SetBlackboard("Set", config=config)
        node.execute_tick()
        # None is a valid value; key should exist
        assert bb.has("k")

    def test_overwrites_existing_key(self):
        bb = Blackboard.create("sb_overwrite")
        bb.set("k", "old")
        config = NodeConfig(blackboard=bb, params={"key": "k", "value": "new"})
        node = SetBlackboard("Set", config=config)
        node.execute_tick()
        assert bb.get("k") == "new"


class TestCheckBlackboard:
    def test_success_when_key_exists(self):
        bb = Blackboard.create("cb_exists")
        bb.set("present", True)
        config = NodeConfig(blackboard=bb, params={"key": "present"})
        node = CheckBlackboard("Check", config=config)
        assert node.execute_tick() == NodeStatus.SUCCESS

    def test_failure_when_key_missing(self):
        bb = Blackboard.create("cb_missing")
        config = NodeConfig(blackboard=bb, params={"key": "absent"})
        node = CheckBlackboard("Check", config=config)
        assert node.execute_tick() == NodeStatus.FAILURE

    def test_failure_when_no_blackboard(self):
        config = NodeConfig(params={"key": "k"})
        node = CheckBlackboard("Check", config=config)
        assert node.execute_tick() == NodeStatus.FAILURE

    def test_failure_when_key_port_missing(self):
        bb = Blackboard.create("cb_no_port")
        config = NodeConfig(blackboard=bb, params={})
        node = CheckBlackboard("Check", config=config)
        assert node.execute_tick() == NodeStatus.FAILURE

    def test_sets_feedback_message_on_success(self):
        bb = Blackboard.create("cb_msg_ok")
        bb.set("x", 99)
        config = NodeConfig(blackboard=bb, params={"key": "x"})
        node = CheckBlackboard("Check", config=config)
        node.execute_tick()
        assert "x" in node.feedback_message

    def test_sets_feedback_message_on_failure(self):
        bb = Blackboard.create("cb_msg_fail")
        config = NodeConfig(blackboard=bb, params={"key": "missing"})
        node = CheckBlackboard("Check", config=config)
        node.execute_tick()
        assert node.feedback_message != ""

    def test_set_then_check_integration(self):
        bb = Blackboard.create("cb_integration")
        set_cfg   = NodeConfig(blackboard=bb, params={"key": "flag", "value": True})
        check_cfg = NodeConfig(blackboard=bb, params={"key": "flag"})
        setter  = SetBlackboard("Set",   config=set_cfg)
        checker = CheckBlackboard("Check", config=check_cfg)
        seq = SequenceNode("Seq", children=[setter, checker])
        assert seq.execute_tick() == NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# tip()
# ─────────────────────────────────────────────────────────────────────────────

class TestTip:
    def test_returns_none_before_any_tick(self):
        root = SequenceNode("s", children=[_Running("a"), _Running("b")])
        assert root.tip() is None

    def test_returns_none_when_all_idle(self):
        leaf = _Success("leaf")
        # Never ticked — IDLE
        assert leaf.tip() is None

    def test_leaf_running_returns_self(self):
        leaf = _Running("leaf")
        leaf.execute_tick()
        assert leaf.tip() is leaf

    def test_leaf_success_returns_none(self):
        leaf = _Success("leaf")
        leaf.execute_tick()
        assert leaf.tip() is None

    def test_leaf_failure_returns_none(self):
        leaf = _Failure("leaf")
        leaf.execute_tick()
        assert leaf.tip() is None

    def test_sequence_returns_running_child(self):
        action = _Running("act")
        seq = SequenceNode("seq", children=[
            FunctionCondition("cond", lambda n: True),
            action,
        ])
        seq.execute_tick()
        assert seq.tip() is action

    def test_sequence_none_after_completion(self):
        seq = SequenceNode("seq", children=[
            AlwaysSuccess("a"),
            AlwaysSuccess("b"),
        ])
        seq.execute_tick()
        assert seq.tip() is None

    def test_fallback_returns_running_child(self):
        action = _Running("act")
        fb = FallbackNode("fb", children=[
            FunctionCondition("fail", lambda n: False),
            action,
        ])
        fb.execute_tick()
        assert fb.tip() is action

    def test_fallback_none_when_first_child_succeeds(self):
        fb = FallbackNode("fb", children=[
            AlwaysSuccess("first"),
            _Running("never_reached"),
        ])
        fb.execute_tick()
        # Tree is SUCCESS — tip is None
        assert fb.tip() is None

    def test_decorator_delegates_to_running_child(self):
        action = _Running("act")
        inv = Inverter("inv", child=action)
        inv.execute_tick()
        assert inv.tip() is action

    def test_decorator_returns_self_when_running_and_child_done(self):
        """Inverter wrapping FAILURE becomes SUCCESS — tip is None (not RUNNING)."""
        inv = Inverter("inv", child=_Failure("f"))
        inv.execute_tick()
        # Inverter status is SUCCESS — tip returns None
        assert inv.tip() is None

    def test_nested_tree_drills_to_leaf(self):
        action = _Running("deep")
        root = SequenceNode("root", children=[
            FunctionCondition("c", lambda n: True),
            SequenceNode("inner", children=[
                FunctionCondition("c2", lambda n: True),
                action,
            ]),
        ])
        root.execute_tick()
        assert root.tip() is action

    def test_parallel_returns_first_running_branch(self):
        a = _Running("a")
        b = _Running("b")
        par = ParallelNode("par", children=[a, b],
                           policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        par.execute_tick()
        # First RUNNING child encountered is a
        assert par.tip() is a

    def test_tree_tip_shortcut(self):
        action = _Running("act")
        seq = SequenceNode("seq", children=[
            FunctionCondition("c", lambda n: True),
            action,
        ])
        tree = _make_tree(seq)
        tree.tick_once()
        assert tree.tip() is action

    def test_control_node_returns_self_when_running_no_running_children(self):
        """ControlNode falls back to self if RUNNING but no child returns tip."""
        seq = SequenceNode("seq", children=[_Running("r")])
        seq.execute_tick()
        # The running child should be returned, not seq itself
        assert seq.tip() is seq._children[0]


# ─────────────────────────────────────────────────────────────────────────────
# ascii_tree() / print_tree()
# ─────────────────────────────────────────────────────────────────────────────

class TestAsciiTree:
    def test_single_node_contains_name(self):
        out = ascii_tree(_Success("Root"))
        assert "Root" in out

    def test_single_node_no_connector(self):
        out = ascii_tree(_Success("Root"))
        assert "├" not in out
        assert "└" not in out

    def test_children_use_connectors(self):
        root = SequenceNode("Root", children=[_Success("A"), _Success("B")])
        out = ascii_tree(root)
        assert "├" in out or "└" in out
        assert "A" in out
        assert "B" in out

    def test_last_child_uses_corner_connector(self):
        root = SequenceNode("Root", children=[_Success("A"), _Success("B")])
        out = ascii_tree(root)
        lines = out.splitlines()
        # Last child line must use └
        assert any("└" in l and "B" in l for l in lines)

    def test_non_last_child_uses_tee_connector(self):
        root = SequenceNode("Root", children=[
            _Success("A"), _Success("B"), _Success("C")
        ])
        out = ascii_tree(root)
        lines = out.splitlines()
        assert any("├" in l and "A" in l for l in lines)

    def test_deep_nesting_prefix_continuation(self):
        inner = SequenceNode("Inner", children=[_Success("Leaf")])
        root = SequenceNode("Root", children=[inner])
        out = ascii_tree(root)
        # Leaf line must have extended prefix (│ or spaces)
        lines = out.splitlines()
        leaf_line = next(l for l in lines if "Leaf" in l)
        assert len(leaf_line) > len("    Leaf")  # has indentation

    def test_show_status_true_includes_status(self):
        n = _Success("n")
        n.execute_tick()
        out = ascii_tree(n, show_status=True)
        assert "SUCCESS" in out

    def test_show_status_false_excludes_status(self):
        n = _Success("n")
        n.execute_tick()
        out = ascii_tree(n, show_status=False)
        assert "SUCCESS" not in out
        assert "RUNNING" not in out
        assert "FAILURE" not in out

    def test_feedback_message_included_when_set(self):
        class _MsgNode(ActionNode):
            def tick(self):
                self.set_feedback_message("doing the thing")
                return NodeStatus.RUNNING

        n = _MsgNode("n")
        n.execute_tick()
        out = ascii_tree(n)
        assert "doing the thing" in out

    def test_feedback_message_absent_when_empty(self):
        n = _Success("n")
        n.execute_tick()
        out = ascii_tree(n)
        # No trailing double-space from empty message
        assert "  " not in out.split("[SUCCESS]")[-1]

    def test_status_symbols_present(self):
        n = _Running("r")
        n.execute_tick()
        out = ascii_tree(n)
        assert "→" in out

        n2 = _Success("s")
        n2.execute_tick()
        assert "✓" in ascii_tree(n2)

        n3 = _Failure("f")
        n3.execute_tick()
        assert "✗" in ascii_tree(n3)

    def test_idle_symbol(self):
        n = _Success("n")
        # Never ticked — IDLE
        assert "○" in ascii_tree(n)

    def test_print_tree_writes_to_stdout(self, capsys):
        root = SequenceNode("Root", children=[_Success("A")])
        print_tree(root)
        captured = capsys.readouterr().out
        assert "Root" in captured
        assert "A" in captured

    def test_tree_ascii_tree_shortcut(self):
        root = SequenceNode("Root", children=[_Success("A")])
        tree = _make_tree(root)
        out = tree.ascii_tree()
        assert "Root" in out
        assert "A" in out

    def test_multiline_output_structure(self):
        root = SequenceNode("Root", children=[
            _Success("A"), _Success("B"), _Success("C")
        ])
        lines = ascii_tree(root).splitlines()
        assert len(lines) == 4  # root + 3 children


# ─────────────────────────────────────────────────────────────────────────────
# Clock protocol + WallClock
# ─────────────────────────────────────────────────────────────────────────────

class TestClock:
    def test_wallclock_returns_float(self):
        assert isinstance(WallClock().monotonic(), float)

    def test_wallclock_is_monotonic(self):
        clk = WallClock()
        t1 = clk.monotonic()
        t2 = clk.monotonic()
        assert t2 >= t1

    def test_wallclock_satisfies_clock_protocol(self):
        assert isinstance(WallClock(), Clock)

    def test_fake_clock_satisfies_clock_protocol(self):
        assert isinstance(FakeClock(), Clock)

    def test_custom_clock_accepted_by_timeout(self):
        clk = FakeClock()
        t = Timeout("t", _Running("c"), duration=1.0, clock=clk)
        assert t._clock is clk

    def test_custom_clock_accepted_by_rate_controller(self):
        clk = FakeClock()
        r = RateController("r", _Running("c"), hz=10.0, clock=clk)
        assert r._clock is clk


# ─────────────────────────────────────────────────────────────────────────────
# Timeout with injected clock
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeoutWithClock:
    def test_default_no_clock_arg_does_not_raise(self):
        t = Timeout("t", _Running("c"), duration=1.0)
        t.execute_tick()  # must not raise

    def test_no_timeout_before_duration(self):
        clk = FakeClock(start=0.0)
        t = Timeout("t", _Running("c"), duration=5.0, clock=clk)
        clk.advance(3.0)
        assert t.execute_tick() == NodeStatus.RUNNING

    def test_timeout_fires_after_duration(self):
        clk = FakeClock(start=0.0)
        t = Timeout("t", _Running("c"), duration=2.0, clock=clk)
        t.execute_tick()       # starts timer at t=0
        clk.advance(3.0)       # now t=3 > duration=2
        assert t.execute_tick() == NodeStatus.FAILURE

    def test_timeout_exact_boundary_not_fired(self):
        """Timeout uses strict >, so equal duration does not fire."""
        clk = FakeClock(start=0.0)
        t = Timeout("t", _Running("c"), duration=2.0, clock=clk)
        t.execute_tick()
        clk.advance(2.0)       # exactly at boundary — not > duration
        assert t.execute_tick() == NodeStatus.RUNNING

    def test_timeout_passes_through_child_success(self):
        clk = FakeClock(start=0.0)
        t = Timeout("t", _Success("c"), duration=5.0, clock=clk)
        assert t.execute_tick() == NodeStatus.SUCCESS

    def test_timeout_passes_through_child_failure(self):
        clk = FakeClock(start=0.0)
        t = Timeout("t", _Failure("c"), duration=5.0, clock=clk)
        assert t.execute_tick() == NodeStatus.FAILURE

    def test_on_halt_resets_start_time(self):
        clk = FakeClock(start=0.0)
        t = Timeout("t", _Running("c"), duration=2.0, clock=clk)
        t.execute_tick()       # starts timer
        t.halt()
        assert t._start_time is None

    def test_timer_restarts_after_halt(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.RUNNING)
        t = Timeout("t", child, duration=2.0, clock=clk)

        t.execute_tick()       # timer starts at 0
        clk.advance(3.0)       # would timeout
        t.halt()               # reset

        # Re-tick: timer restarts at current clock (3.0)
        assert t.execute_tick() == NodeStatus.RUNNING

        clk.advance(3.0)       # now at 6.0, > 3.0+2.0=5.0
        assert t.execute_tick() == NodeStatus.FAILURE

    def test_halts_child_on_timeout(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.RUNNING)
        t = Timeout("t", child, duration=1.0, clock=clk)
        t.execute_tick()
        clk.advance(2.0)
        t.execute_tick()  # fires timeout
        assert child.status == NodeStatus.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# RateController with injected clock
# ─────────────────────────────────────────────────────────────────────────────

class TestRateControllerWithClock:
    def test_default_no_clock_arg_does_not_raise(self):
        r = RateController("r", _Running("c"), hz=10.0)
        r.execute_tick()

    def test_first_tick_always_propagates(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.SUCCESS)
        r = RateController("r", child, hz=1.0, clock=clk)
        assert r.execute_tick() == NodeStatus.SUCCESS

    def test_second_tick_within_period_not_propagated(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.SUCCESS)
        r = RateController("r", child, hz=1.0, clock=clk)  # period = 1.0s
        r.execute_tick()
        clk.advance(0.5)   # within period
        r.execute_tick()
        assert child.tick_count_local == 1  # child ticked only once

    def test_tick_after_period_elapsed_propagates(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.SUCCESS)
        r = RateController("r", child, hz=1.0, clock=clk)
        r.execute_tick()
        clk.advance(1.0)   # exactly at period boundary
        r.execute_tick()
        assert child.tick_count_local == 2

    def test_returns_last_status_between_ticks(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.RUNNING)
        r = RateController("r", child, hz=1.0, clock=clk)
        r.execute_tick()
        clk.advance(0.3)
        # Not time yet — should return RUNNING (last status)
        assert r.execute_tick() == NodeStatus.RUNNING
        assert child.tick_count_local == 1

    def test_on_halt_resets_state(self):
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.RUNNING)  # keep rate controller RUNNING so halt fires _on_halt
        r = RateController("r", child, hz=1.0, clock=clk)
        r.execute_tick()          # ticks child (count=1), r._status=RUNNING
        clk.advance(0.5)
        r.halt()                  # _status==RUNNING → _on_halt() fires, resets _last_tick=None
        # After halt, next tick must re-tick child immediately regardless of elapsed time
        r.execute_tick()
        assert child.tick_count_local == 2

    def test_high_hz_ticks_every_call(self):
        """At 1000 Hz the period is 1ms; advancing 10ms allows 10 ticks."""
        clk = FakeClock(start=0.0)
        child = MockActionNode("c")
        child.set_status(NodeStatus.RUNNING)
        r = RateController("r", child, hz=1000.0, clock=clk)
        for _ in range(10):
            clk.advance(0.001)
            r.execute_tick()
        assert child.tick_count_local == 10
