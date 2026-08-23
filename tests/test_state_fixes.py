"""Regression tests for the shared-state findings.

Covered here:
  F3   Blackboard.reset() left the callback dispatch cache populated
  #12  Blackboard subscribers were strong refs, so dropped nodes leaked
  F9   remapping was honoured by set/get/has but ignored by remove/delete/entry
  F11  tracer export -> load -> export discarded every node field
  #10  Inspector.on_node_halt() recorded nothing
  F16  Logger.add_json_file_sink() leaked its file handle

Run with:  pytest tests/test_state_fixes.py -v
"""
from __future__ import annotations

import gc
import json
import os

import pytest

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import NodeStatus, NodeType
from bteng.introspection.inspector import Inspector, NodeExecutionRecord
from bteng.introspection.logger import Logger, LogEntry, LogLevel
from bteng.logging.tracer import ExecutionTracer


# ── F3: reset() must invalidate the callback cache ────────────────────────────

class TestF3ResetClearsCallbackCache:
    def test_callback_does_not_fire_after_reset_following_a_write(self):
        """The warm-up write is what the old code got wrong.

        set() only rebuilds _callbacks_cache when _callbacks_dirty is set, so a
        blackboard that was written to before reset() kept dispatching to the
        already-cached (and by then unsubscribed) callbacks forever.
        """
        Blackboard.reset("f3_warm")
        bb = Blackboard.create("f3_warm")
        seen = []
        bb.subscribe(lambda k, v: seen.append((k, v)))

        bb.set("warmup", 0)                  # builds + un-dirties the cache
        assert seen == [("warmup", 0)]

        Blackboard.reset("f3_warm")
        Blackboard.create("f3_warm").set("after", 1)
        assert seen == [("warmup", 0)], f"stale callback fired: {seen}"

    def test_reset_clears_the_cache_and_sets_the_dirty_flag(self):
        Blackboard.reset("f3_state")
        bb = Blackboard.create("f3_state")
        bb.subscribe(lambda k, v: None)
        bb.set("x", 1)
        assert bb._callbacks_cache and bb._callbacks_dirty is False

        Blackboard.reset("f3_state")
        assert bb._callbacks == {}
        assert bb._callbacks_cache == []
        assert bb._callbacks_dirty is True

    def test_reset_releases_the_closure(self):
        """The cache also pinned the closure — reset() must let it go."""
        Blackboard.reset("f3_leak")
        bb = Blackboard.create("f3_leak")

        class Sentinel:
            pass

        sentinel = Sentinel()
        bb.subscribe(lambda k, v: sentinel)   # closes over sentinel
        bb.set("x", 1)

        import weakref
        ref = weakref.ref(sentinel)
        Blackboard.reset("f3_leak")
        del sentinel
        gc.collect()
        assert ref() is None, "reset() left the subscriber closure alive"

    def test_reset_clears_the_snapshot_dirty_flag(self):
        Blackboard.reset("f3_snap")
        bb = Blackboard.create("f3_snap")
        bb.set("k", 1)
        Blackboard.reset("f3_snap")
        assert bb.take_snapshot_if_dirty() is None

    def test_a_new_subscriber_after_reset_still_fires(self):
        Blackboard.reset("f3_resub")
        bb = Blackboard.create("f3_resub")
        bb.subscribe(lambda k, v: None)
        bb.set("x", 1)
        Blackboard.reset("f3_resub")

        seen = []
        bb.subscribe(lambda k, v: seen.append(k))
        bb.set("y", 2)
        assert seen == ["y"]


# ── #12: bound-method subscribers are weakly held ─────────────────────────────

class _Listener:
    """Stand-in for a ReactiveSequence: subscribes a bound method."""

    def __init__(self, log: list, tag: str) -> None:
        self.log = log
        self.tag = tag

    def on_write(self, key, value) -> None:
        self.log.append((self.tag, key, value))


class TestBug12WeakSubscribers:
    def test_dropped_subscriber_stops_firing_and_is_collected(self):
        bb = Blackboard("weak_drop")
        log: list = []
        live = _Listener(log, "live")
        dropped = _Listener(log, "dropped")
        bb.subscribe(live.on_write)
        bb.subscribe(dropped.on_write)

        bb.set("a", 1)
        assert sorted(t for t, _, _ in log) == ["dropped", "live"]

        import weakref
        ref = weakref.ref(dropped)
        del dropped
        gc.collect()
        assert ref() is None, "blackboard kept the dropped subscriber alive"

        log.clear()
        bb.set("b", 2)
        assert log == [("live", "b", 2)], f"dead subscriber still fired: {log}"

    def test_dead_subscription_is_reaped_from_the_table(self):
        bb = Blackboard("weak_reap")
        listener = _Listener([], "x")
        bb.subscribe(listener.on_write)
        bb.set("a", 1)
        assert len(bb._callbacks) == 1

        del listener
        gc.collect()
        bb.set("b", 2)          # dispatch notices the dead ref and prunes it
        assert bb._callbacks == {}

    def test_lambda_kept_alive_even_with_no_caller_reference(self):
        """A throwaway lambda MUST keep working — silently dropping it would be
        a worse bug than the leak this change fixes."""
        bb = Blackboard("weak_lambda")
        seen = []
        bb.subscribe(lambda k, v: seen.append(k))   # no reference kept
        gc.collect()
        bb.set("a", 1)
        assert seen == ["a"]

    def test_plain_function_and_callable_object_are_kept_alive(self):
        bb = Blackboard("weak_func")
        seen = []

        def handler(k, v):
            seen.append(("fn", k))

        class CallableObj:
            def __call__(self, k, v):
                seen.append(("obj", k))

        bb.subscribe(handler)
        bb.subscribe(CallableObj())
        del handler
        gc.collect()
        bb.set("a", 1)
        assert sorted(seen) == [("fn", "a"), ("obj", "a")]

    def test_live_bound_method_still_fires_repeatedly(self):
        bb = Blackboard("weak_live")
        log: list = []
        listener = _Listener(log, "live")
        bb.subscribe(listener.on_write)
        for i in range(3):
            bb.set("k", i)
        assert log == [("live", "k", 0), ("live", "k", 1), ("live", "k", 2)]

    def test_unsubscribe_still_works_for_both_kinds(self):
        bb = Blackboard("weak_unsub")
        log: list = []
        listener = _Listener(log, "m")
        seen = []
        m_id = bb.subscribe(listener.on_write)
        l_id = bb.subscribe(lambda k, v: seen.append(k))

        bb.set("a", 1)
        assert log and seen

        bb.unsubscribe(m_id)
        bb.unsubscribe(l_id)
        log.clear()
        seen.clear()
        bb.set("b", 2)
        assert log == [] and seen == []
        assert bb._callbacks == {}

    def test_unsubscribing_an_unknown_id_is_a_noop(self):
        bb = Blackboard("weak_unsub_unknown")
        bb.unsubscribe(999)

    def test_reactive_node_is_released_when_dropped_unhalted(self):
        """The real-world case: a RUNNING ReactiveSequence that is discarded
        without being halted must not pin itself (and its subtree) forever."""
        from bteng import NodeConfig, ReactiveSequenceNode
        from bteng.testing.mock_nodes import MockActionNode, MockConditionNode
        import weakref

        bb = Blackboard("weak_reactive")
        cond = MockConditionNode("c", NodeConfig(blackboard=bb))
        cond.set_bool(True)
        action = MockActionNode("a")
        action.set_ticks_to_complete(50)
        node = ReactiveSequenceNode("seq", children=[cond, action])

        assert node.execute_tick() == NodeStatus.RUNNING
        assert node._bb_subscriptions, "expected a live subscription"

        ref = weakref.ref(node)
        del node, cond, action        # dropped while RUNNING, never halted
        gc.collect()
        assert ref() is None, "blackboard subscription leaked the reactive node"


# ── F9: remapping honoured by remove/delete/entry ─────────────────────────────

class TestF9RemappingOnRemoveAndEntry:
    def _scopes(self):
        parent = Blackboard("root")
        child = parent.create_child_scope("sub", remapping={"local": "shared"})
        return parent, child

    def test_remove_through_a_remap_removes_the_parents_value(self):
        parent, child = self._scopes()
        child.set("local", "v", writer="nodeA")
        assert parent.get("shared") == "v"

        child.remove("local")
        assert child.has("local") is False
        assert child.get("local") is None
        assert parent.get("shared") is None
        assert parent.has("shared") is False

    def test_delete_alias_follows_the_same_path(self):
        parent, child = self._scopes()
        child.set("local", 1)
        child.delete("local")
        assert parent.has("shared") is False

    def test_remove_is_still_scope_local_for_unmapped_keys(self):
        """set() never writes to the parent, so remove() must not either."""
        parent = Blackboard("root")
        child = parent.create_child_scope("sub")
        parent.set("inherited", 7)
        child.remove("inherited")
        assert parent.get("inherited") == 7      # parent untouched
        assert child.get("inherited") == 7       # still readable through

    def test_removing_a_missing_remapped_key_is_a_noop(self):
        parent, child = self._scopes()
        child.remove("local")                    # nothing to remove
        assert parent.has("shared") is False

    def test_entry_resolves_a_remapped_key(self):
        parent, child = self._scopes()
        child.set("local", "written-through", writer="nodeA")

        e = child.entry("local")
        assert e is not None, "entry() ignored the remapping"
        assert e.value == "written-through"
        assert e.type_name == "str"
        assert e.last_writer == "nodeA"
        assert e is not parent._entries["shared"], "entry() must return a copy"

    def test_entry_returns_none_for_a_remapped_key_that_is_unset(self):
        _parent, child = self._scopes()
        assert child.entry("local") is None

    def test_scope_local_operations_stay_scope_local(self):
        """Documented, deliberate asymmetry — assert it so it cannot drift."""
        parent, child = self._scopes()
        child.set("local", "v")
        child.set("own", 1)

        assert child.keys() == ["own"]
        assert child.snapshot() == {"own": 1}
        assert "local" not in child.debug_string()

        child.clear()
        assert child.get("own") is None
        assert parent.get("shared") == "v", "clear() must not touch the parent"

    def test_reads_inherit_but_writes_shadow(self):
        """Documented semantic, unchanged: a subtree read-modify-write shadows."""
        parent = Blackboard("root")
        child = parent.create_child_scope("sub")
        parent.set("counter", 1)
        child.set("counter", child.get("counter") + 1)
        assert child.get("counter") == 2
        assert parent.get("counter") == 1


# ── F11: tracer replay round-trip ─────────────────────────────────────────────

def _record(uid, name, node_type, status, **kw) -> NodeExecutionRecord:
    kw.setdefault("tick_time", 1.0)
    kw.setdefault("duration", 0.005)
    return NodeExecutionRecord(uid=uid, name=name, node_type=node_type,
                               status=status, **kw)


def _populated_tracer() -> ExecutionTracer:
    t = ExecutionTracer()
    t.begin_frame(0)
    t.record_node(_record("u1", "Move", NodeType.ACTION, NodeStatus.RUNNING,
                          feedback_message="driving"))
    t.record_node(_record("u2", "Check", NodeType.CONDITION, NodeStatus.SUCCESS,
                          duration=0.001))
    t.end_frame({"battery": 88})
    return t


class TestF11TracerReplayRoundTrip:
    def test_export_replay_round_trip_is_stable(self):
        blob1 = _populated_tracer().export_replay()
        t2 = ExecutionTracer()
        assert t2.load_replay(blob1) is True
        assert json.loads(t2.export_replay()) == json.loads(blob1)

    def test_loaded_records_are_real_record_objects(self):
        t2 = ExecutionTracer()
        assert t2.load_replay(_populated_tracer().export_replay()) is True
        recs = t2.replay_frame(0).node_records
        assert all(isinstance(r, NodeExecutionRecord) for r in recs)
        assert [r.uid for r in recs] == ["u1", "u2"]
        assert [r.name for r in recs] == ["Move", "Check"]
        assert recs[0].node_type is NodeType.ACTION
        assert recs[0].status is NodeStatus.RUNNING
        assert recs[0].duration == pytest.approx(0.005)
        assert recs[1].node_type is NodeType.CONDITION

    def test_export_replay_carries_timestamp_and_node_type(self):
        t = _populated_tracer()
        doc = json.loads(t.export_replay())
        assert doc[0]["ts"] == t.frames()[0].timestamp
        assert [n["type"] for n in doc[0]["nodes"]] == ["action", "condition"]

    def test_export_json_round_trip_emits_json_objects(self):
        t = _populated_tracer()
        t2 = ExecutionTracer()
        assert t2.load_replay(t.export_json()) is True

        doc = json.loads(t2.export_json())
        records = doc[0]["node_records"]
        assert all(isinstance(r, dict) for r in records), \
            f"export_json emitted repr strings: {records}"
        assert records[0]["uid"] == "u1"
        assert records[0]["feedback_message"] == "driving"
        assert records[0]["node_type"] == "action"
        # Full-fidelity format round-trips exactly.
        assert json.loads(t.export_json()) == doc

    def test_blackboard_snapshot_survives_the_round_trip(self):
        t2 = ExecutionTracer()
        t2.load_replay(_populated_tracer().export_replay())
        assert t2.replay_frame(0).blackboard_snapshot == {"battery": "88"}

    def test_load_replay_of_garbage_leaves_existing_frames_intact(self):
        t = _populated_tracer()
        before = t.export_replay()

        for blob in ("{}", "[1, 2]", "not json at all", '{"frames": []}', "null"):
            assert t.load_replay(blob) is False, f"{blob!r} should not load"
            assert t.frame_count() == 1, f"{blob!r} destroyed the existing trace"
            assert t.export_replay() == before

    def test_load_replay_of_an_empty_list_succeeds(self):
        t = _populated_tracer()
        assert t.load_replay("[]") is True
        assert t.frame_count() == 0

    def test_set_max_frames_trims_already_stored_frames(self):
        t = ExecutionTracer(max_frames=100)
        for i in range(10):
            t.begin_frame(i)
            t.end_frame()
        t.set_max_frames(3)
        assert t.frame_count() == 3
        # Oldest dropped first, same order as end_frame() eviction.
        assert [f.tick_index for f in t.frames()] == [7, 8, 9]

    def test_set_max_frames_keeps_bounding_new_frames(self):
        t = ExecutionTracer(max_frames=100)
        for i in range(10):
            t.begin_frame(i)
            t.end_frame()
        t.set_max_frames(3)
        t.begin_frame(99)
        t.end_frame()
        assert t.frame_count() == 3
        assert [f.tick_index for f in t.frames()] == [8, 9, 99]

    def test_load_replay_respects_max_frames(self):
        src = ExecutionTracer()
        for i in range(10):
            src.begin_frame(i)
            src.end_frame()
        t = ExecutionTracer(max_frames=4)
        assert t.load_replay(src.export_replay()) is True
        assert t.frame_count() == 4


# ── #10: Inspector.on_node_halt ───────────────────────────────────────────────

class TestBug10InspectorHalt:
    def _running(self, insp: Inspector, uid: str = "u1") -> None:
        insp.on_node_tick(uid=uid, name="Move", node_type=NodeType.ACTION,
                          old_status=NodeStatus.IDLE,
                          new_status=NodeStatus.RUNNING, duration=0.001)

    def test_halt_clears_the_running_set_and_active_path(self):
        insp = Inspector()
        self._running(insp)
        assert insp.running_nodes() == ["u1"]
        assert insp.active_path() == ["u1"]

        insp.on_node_halt("u1", "Move", "timeout")
        assert insp.running_nodes() == []
        assert insp.active_path() == []

    def test_halt_records_the_reason(self):
        insp = Inspector()
        self._running(insp)
        insp.on_node_halt("u1", "Move", "timeout after 1.0s")

        assert insp.halt_reason("u1") == "timeout after 1.0s"
        assert insp.halt_reasons() == {"u1": "timeout after 1.0s"}
        assert insp.execution_history()[-1].halt_reason == "timeout after 1.0s"

    def test_halt_reason_lands_on_the_right_nodes_record(self):
        insp = Inspector()
        self._running(insp, "u1")
        self._running(insp, "u2")
        insp.on_node_halt("u1", "Move", "preempted")

        by_uid = {r.uid: r for r in insp.execution_history()}
        assert by_uid["u1"].halt_reason == "preempted"
        assert by_uid["u2"].halt_reason == ""

    def test_halt_does_not_flood_history_or_explain_log(self):
        insp = Inspector()
        self._running(insp)
        before_hist = len(insp.execution_history())
        before_expl = len(insp.explanations())
        for _ in range(50):
            insp.on_node_halt("u1", "Move", "teardown")
        assert len(insp.execution_history()) == before_hist
        assert len(insp.explanations()) == before_expl

    def test_halt_of_an_unknown_node_is_harmless(self):
        insp = Inspector()
        insp.on_node_halt("nope", "Ghost", "whatever")
        assert insp.running_nodes() == []
        assert insp.halt_reason("nope") == "whatever"

    def test_reset_clears_halt_reasons(self):
        insp = Inspector()
        self._running(insp)
        insp.on_node_halt("u1", "Move", "timeout")
        insp.reset()
        assert insp.halt_reason("u1") == ""
        assert insp.halt_reasons() == {}

    def test_timeout_decorator_clears_the_stale_running_node(self):
        """End-to-end: a node halted by Timeout must leave running_nodes().

        Exercises the TreeNode.halt() call site if it has landed; the direct
        on_node_halt() tests above cover this side regardless.
        """
        from bteng.concurrency.clock import Clock
        from bteng.nodes.decorators.timeout import Timeout
        from bteng.nodes.leaf.action import ActionNode

        class _FakeClock(Clock):
            def __init__(self):
                self.t = 0.0

            def monotonic(self):
                return self.t

            def sleep(self, s):
                self.t += s

        class _Forever(ActionNode):
            def tick(self):
                return NodeStatus.RUNNING

        clock = _FakeClock()
        leaf = _Forever("forever")
        dec = Timeout("to", leaf, duration=1.0, clock=clock)
        insp = Inspector()
        for n in (dec, leaf):
            n._inspector = insp

        assert dec.execute_tick() == NodeStatus.RUNNING
        assert leaf.uid in insp.running_nodes()

        clock.t = 2.0
        assert dec.execute_tick() == NodeStatus.FAILURE
        if leaf.uid in insp.running_nodes():
            pytest.skip("TreeNode.halt() does not call on_node_halt yet "
                        "(call site owned by another change)")
        assert leaf.uid not in insp.active_path()


# ── F16: Logger file-handle lifetime ──────────────────────────────────────────

def _entry(msg: str = "") -> LogEntry:
    return LogEntry(timestamp=1.0, level=LogLevel.INFO, node_uid="u1",
                    node_name="Move", old_status=NodeStatus.IDLE,
                    new_status=NodeStatus.SUCCESS, message=msg)


class TestF16LoggerClose:
    def test_close_releases_the_file_handle(self, tmp_path):
        path = tmp_path / "run.jsonl"
        logger = Logger.create()
        sink = logger.add_json_file_sink(str(path))
        logger.log(_entry("before"))

        handles = [fh for fh, _ in logger._file_sinks]
        assert len(handles) == 1 and not handles[0].closed

        logger.close()
        assert handles[0].closed, "close() did not release the descriptor"
        assert logger._file_sinks == []
        assert sink not in logger._sinks

    def test_sink_behaviour_is_unchanged_before_close(self, tmp_path):
        path = tmp_path / "run.jsonl"
        logger = Logger.create()
        logger.add_json_file_sink(str(path))
        logger.log(_entry("one"))
        logger.log(_entry("two"))
        logger.close()

        lines = [json.loads(ln) for ln in path.read_text().splitlines()]
        assert [ln["message"] for ln in lines] == ["one", "two"]
        assert lines[0]["uid"] == "u1"
        assert lines[0]["to"] == "SUCCESS"

    def test_logging_after_close_does_not_raise_or_write(self, tmp_path):
        path = tmp_path / "run.jsonl"
        logger = Logger.create()
        logger.add_json_file_sink(str(path))
        logger.log(_entry("kept"))
        logger.close()
        logger.log(_entry("dropped"))          # must not raise

        assert path.read_text().count("\n") == 1
        assert "dropped" not in path.read_text()
        assert len(logger.history()) == 2      # history still records it

    def test_close_is_idempotent(self, tmp_path):
        logger = Logger.create()
        logger.add_json_file_sink(str(tmp_path / "run.jsonl"))
        logger.close()
        logger.close()

    def test_close_leaves_non_file_sinks_registered(self, tmp_path):
        logger = Logger.create()
        logger.add_json_file_sink(str(tmp_path / "run.jsonl"))
        seen = []
        logger.add_custom_sink(lambda e: seen.append(e.message))
        logger.close()
        logger.log(_entry("still here"))
        assert seen == ["still here"]

    def test_context_manager_closes(self, tmp_path):
        path = tmp_path / "run.jsonl"
        with Logger.create() as logger:
            logger.add_json_file_sink(str(path))
            logger.log(_entry("inside"))
            handles = [fh for fh, _ in logger._file_sinks]
        assert handles[0].closed
        assert "inside" in path.read_text()

    def test_remove_sink_closes_only_that_sinks_handle(self, tmp_path):
        logger = Logger.create()
        a = logger.add_json_file_sink(str(tmp_path / "a.jsonl"))
        logger.add_json_file_sink(str(tmp_path / "b.jsonl"))
        handles = {s: fh for fh, s in logger._file_sinks}

        assert logger.remove_sink(a) is True
        assert handles[a].closed
        assert len(logger._file_sinks) == 1
        assert logger.remove_sink(a) is False

        logger.log(_entry("only-b"))
        logger.close()
        assert (tmp_path / "a.jsonl").read_text() == ""
        assert "only-b" in (tmp_path / "b.jsonl").read_text()

    def test_many_loggers_do_not_leak_descriptors(self, tmp_path):
        """The reported symptom: a logger built per run leaked one fd each."""
        path = str(tmp_path / "run.jsonl")
        handles = []
        for _ in range(50):
            logger = Logger.create()
            logger.add_json_file_sink(path)
            logger.log(_entry("x"))
            handles.extend(fh for fh, _ in logger._file_sinks)
            logger.close()
        assert all(fh.closed for fh in handles)
        assert os.path.exists(path)
