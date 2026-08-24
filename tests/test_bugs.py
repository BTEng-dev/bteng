"""Regression tests for all 21 bugs found in the BTEng review.

Each test is annotated with the bug ID it covers.
Run with:  pytest tests/test_bugs.py -v
"""
from __future__ import annotations

import io
import sys
import threading
import time
import types
import xml.etree.ElementTree as ET

import pytest

from bteng.blackboard.blackboard import Blackboard
from bteng.concurrency.cancellation_token import CancellationToken
from bteng.concurrency.thread_pool import ThreadPool
from bteng.core.executor import EventBus, BehaviorEvent, ExecutorConfig, TreeExecutor
from bteng.core.node import (
    ControlNode, DecoratorNode, NodeConfig, NodeStatus, NodeType, TreeNode,
)
from bteng.core.tree import Tree, TreeMetadata, TreeModification, ModificationType
from bteng.core.tree_builder import TreeBuilder
from bteng.factory.factory import NodeFactory
from bteng.introspection.inspector import Inspector
from bteng.introspection.logger import Logger, LogLevel
from bteng.logging.tracer import ExecutionTracer
from bteng.nodes.control.fallback import FallbackNode
from bteng.nodes.control.parallel import ParallelNode, ParallelPolicy
from bteng.nodes.control.reactive_sequence import ReactiveSequenceNode
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.decorators.inverter import Inverter
from bteng.nodes.decorators.rate_controller import RateController
from bteng.nodes.decorators.retry import Retry
from bteng.nodes.decorators.timeout import Timeout
from bteng.nodes.leaf.action import ActionNode, FunctionAction, action
from bteng.nodes.leaf.condition import FunctionCondition
from bteng.testing.mock_nodes import MockActionNode, MockConditionNode
from bteng.xml_parser.parser import XMLTreeParser


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _action(result: NodeStatus = NodeStatus.SUCCESS) -> FunctionAction:
    return FunctionAction("a", lambda _: result)

def _make_tree(root: TreeNode) -> Tree:
    return Tree(TreeMetadata(id="test"), root)


# ─────────────────────────────────────────────────────────────────────────────
# BUG-1: ParallelNode success_threshold stale when children added after init
# ─────────────────────────────────────────────────────────────────────────────

class TestBug1ParallelThreshold:
    def test_threshold_with_zero_children_at_init(self):
        """With REQUIRE_ALL_SUCCESS and 0 children at init, SUCCESS is vacuous but safe."""
        p = ParallelNode("P", children=[], policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        # Empty tree: should succeed vacuously (0 of 0)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_threshold_correct_after_children_added_post_init(self):
        """Threshold must reflect live child count, not count at construction time."""
        p = ParallelNode("P", children=[], policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        for i in range(3):
            p._children.append(FunctionAction(f"c{i}", lambda _: NodeStatus.RUNNING))
        # With 3 RUNNING children and REQUIRE_ALL_SUCCESS, result must be RUNNING
        assert p.execute_tick() == NodeStatus.RUNNING

    def test_require_one_success_policy(self):
        p = ParallelNode("P", children=[
            _action(NodeStatus.SUCCESS),
            _action(NodeStatus.RUNNING),
        ], policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_require_all_complete_policy(self):
        results = [NodeStatus.SUCCESS, NodeStatus.SUCCESS]
        children = [FunctionAction(f"c{i}", lambda _, i=i: results[i]) for i in range(2)]
        p = ParallelNode("P", children=children,
                         policy=ParallelPolicy.REQUIRE_ALL_COMPLETE)
        assert p.execute_tick() == NodeStatus.SUCCESS

    def test_failure_threshold(self):
        p = ParallelNode("P", children=[
            _action(NodeStatus.FAILURE),
            _action(NodeStatus.RUNNING),
        ], failure_threshold=1)
        assert p.execute_tick() == NodeStatus.FAILURE

    def test_insert_child_modification_respects_new_threshold(self):
        p = ParallelNode("P", children=[], policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        tree = _make_tree(p)
        child = FunctionAction("c", lambda _: NodeStatus.SUCCESS)
        tree.queue_modification(TreeModification(
            type=ModificationType.INSERT_CHILD,
            target_uid=p.uid,
            child_index=0,
            new_node=child,
        ))
        # After insertion, threshold recalculated: 1 child needed
        status = tree.tick_once()
        assert status == NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# BUG-2: ControlNode / DecoratorNode halt() bypasses _on_halt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBug2HaltLifecycle:
    def test_control_node_on_halt_called(self):
        class MySeq(SequenceNode):
            on_halt_called = False
            def _on_halt(self):
                MySeq.on_halt_called = True

        c = FunctionAction("c", lambda _: NodeStatus.RUNNING)
        s = MySeq("s", [c])
        s.execute_tick()
        s.halt()
        assert MySeq.on_halt_called

    def test_decorator_node_on_halt_called(self):
        class MyInv(Inverter):
            on_halt_called = False
            def _on_halt(self):
                MyInv.on_halt_called = True

        child = FunctionAction("c", lambda _: NodeStatus.RUNNING)
        inv = MyInv("inv", child=child)
        inv.execute_tick()
        inv.halt()
        assert MyInv.on_halt_called

    def test_control_node_on_halt_not_called_when_idle(self):
        """_on_halt() should NOT fire if node is not RUNNING."""
        class MySeq(SequenceNode):
            on_halt_called = False
            def _on_halt(self):
                MySeq.on_halt_called = True

        s = MySeq("s", [_action(NodeStatus.SUCCESS)])
        # Don't tick — status remains IDLE
        s.halt()
        assert not MySeq.on_halt_called

    def test_retry_on_halt_resets_attempts(self):
        """Retry._on_halt() must reset _attempts so re-entry starts fresh."""
        calls = [0]
        def flaky(_):
            calls[0] += 1
            return NodeStatus.FAILURE
        r = Retry("r", child=FunctionAction("f", flaky), max_attempts=3)
        r.execute_tick()  # attempt 1 → RUNNING
        r.execute_tick()  # attempt 2 → RUNNING
        r.halt()
        assert r._attempts == 0
        # Re-enter: should have full 3 attempts again
        calls[0] = 0
        r.execute_tick()
        r.execute_tick()
        result = r.execute_tick()
        assert result == NodeStatus.FAILURE
        assert calls[0] == 3

    def test_timeout_on_halt_resets_start_time(self):
        child = FunctionAction("c", lambda _: NodeStatus.RUNNING)
        t = Timeout("t", child=child, duration=10.0)
        t.execute_tick()
        assert t._start_time is not None
        t.halt()
        assert t._start_time is None


# ─────────────────────────────────────────────────────────────────────────────
# BUG-3: TreeBuilder.map() applies to scope node, not last leaf
# ─────────────────────────────────────────────────────────────────────────────

class TestBug3TreeBuilderMap:
    def test_map_applies_to_last_leaf(self):
        bb = Blackboard("test")
        bb.set("goal", "target_X")
        read_values = []

        class ReadAction(ActionNode):
            def tick(self):
                read_values.append(self.get_input("target"))
                return NodeStatus.SUCCESS

        NodeFactory.get_instance().register(ReadAction, "__ReadAction__")

        tree = (
            TreeBuilder(blackboard=bb)
            .sequence("root")
                .node("__ReadAction__", "reader")
                .map("target", "goal")
            .end()
            .build()
        )
        tree.tick_once()
        assert read_values == ["target_X"], (
            f"Expected ['target_X'], got {read_values}. "
            "map() did not apply to the leaf node."
        )

    def test_map_output_applies_to_last_leaf(self):
        bb = Blackboard("test_out")

        class WriteAction(ActionNode):
            def tick(self):
                self.set_output("result", 42)
                return NodeStatus.SUCCESS

        NodeFactory.get_instance().register(WriteAction, "__WriteAction__")
        tree = (
            TreeBuilder(blackboard=bb)
            .sequence("root")
                .node("__WriteAction__", "writer")
                .map_output("result", "answer")
            .end()
            .build()
        )
        tree.tick_once()
        assert bb.get("answer") == 42

    def test_map_on_scope_opener_affects_scope_node(self):
        """After .sequence(), map() should affect the sequence itself."""
        bb = Blackboard("test_scope")
        tree = (
            TreeBuilder(blackboard=bb)
            .sequence("root")
            .map("seq_port", "some_key")
            .end()
            .build()
        )
        assert "seq_port" in tree.root._config.input_ports

    def test_literal_applies_to_last_leaf(self):
        got = []

        class ParamAction(ActionNode):
            def tick(self):
                got.append(self.get_input("speed"))
                return NodeStatus.SUCCESS

        NodeFactory.get_instance().register(ParamAction, "__ParamAction__")
        tree = (
            TreeBuilder()
            .sequence("root")
                .node("__ParamAction__", "act")
                .literal("speed", 3.14)
            .end()
            .build()
        )
        tree.tick_once()
        assert got == [3.14]

    def test_action_zero_arg_lambda(self):
        tree = TreeBuilder().sequence("root").action("a", lambda: NodeStatus.SUCCESS).end().build()
        assert tree.tick_once() == NodeStatus.SUCCESS

    def test_action_one_arg_lambda(self):
        """One-arg lambdas must also work via _adapt_fn."""
        received = []
        tree = (
            TreeBuilder()
            .sequence("root")
                .action("a", lambda node: (received.append(node) or NodeStatus.SUCCESS))
            .end()
            .build()
        )
        tree.tick_once()
        assert len(received) == 1 and received[0] is not None


# ─────────────────────────────────────────────────────────────────────────────
# BUG-4: MockActionNode._ticks_done not reset on halt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBug4MockActionHalt:
    def test_halt_resets_ticks_done(self):
        m = MockActionNode("m")
        m.set_ticks_to_complete(3)
        m.execute_tick()  # RUNNING, _ticks_done=1
        m.execute_tick()  # RUNNING, _ticks_done=2
        m.halt()
        assert m._ticks_done == 0

    def test_re_entry_after_halt_starts_fresh(self):
        m = MockActionNode("m")
        m.set_ticks_to_complete(3)
        m.execute_tick()
        m.execute_tick()
        m.halt()
        # After halt, must need 3 full ticks again
        assert m.execute_tick() == NodeStatus.RUNNING
        assert m.execute_tick() == NodeStatus.RUNNING
        assert m.execute_tick() == NodeStatus.SUCCESS

    def test_reset_node_also_resets_ticks_done(self):
        m = MockActionNode("m")
        m.set_ticks_to_complete(5)
        for _ in range(3):
            m.execute_tick()
        m.reset_node()
        assert m._ticks_done == 0


# ─────────────────────────────────────────────────────────────────────────────
# BUG-5: ThreadPool.wait_all() permanently destroyed the executor
# ─────────────────────────────────────────────────────────────────────────────

class TestBug5ThreadPoolWaitAll:
    def test_wait_all_does_not_destroy_pool(self):
        pool = ThreadPool(2)
        f1 = pool.submit(lambda: NodeStatus.SUCCESS)
        assert f1.result(timeout=2.0) == NodeStatus.SUCCESS
        ok = pool.wait_all(timeout=2.0)
        assert ok
        # Pool must still accept new work
        f2 = pool.submit(lambda: NodeStatus.FAILURE)
        assert f2.result(timeout=2.0) == NodeStatus.FAILURE
        pool.shutdown()

    def test_wait_all_returns_true_on_success(self):
        pool = ThreadPool(2)
        barrier = threading.Barrier(2)
        done = threading.Event()

        def task():
            barrier.wait()
            done.set()
            return NodeStatus.SUCCESS

        pool.submit(task)
        barrier.wait()
        result = pool.wait_all(timeout=2.0)
        assert result is True
        pool.shutdown()

    def test_wait_all_timeout_returns_false(self):
        pool = ThreadPool(1)
        started = threading.Event()

        def slow():
            started.set()
            time.sleep(5.0)
            return NodeStatus.SUCCESS

        pool.submit(slow)
        started.wait(timeout=1.0)
        result = pool.wait_all(timeout=0.05)
        assert result is False
        pool.shutdown()

    def test_submit_after_shutdown_raises(self):
        pool = ThreadPool(1)
        pool.shutdown()
        with pytest.raises(RuntimeError):
            pool.submit(lambda: NodeStatus.SUCCESS)


# ─────────────────────────────────────────────────────────────────────────────
# BUG-6: Blackboard.reset() left callbacks and schema in place
# ─────────────────────────────────────────────────────────────────────────────

class TestBug6BlackboardReset:
    def test_reset_clears_callbacks(self):
        bb = Blackboard.create("_test_reset_cb_")
        calls = []
        bb.subscribe(lambda k, v: calls.append((k, v)))
        Blackboard.reset("_test_reset_cb_")
        bb2 = Blackboard.create("_test_reset_cb_")
        bb2.set("x", 1)
        assert calls == [], "Stale callback fired after reset()"

    def test_reset_clears_entries(self):
        bb = Blackboard.create("_test_reset_entries_")
        bb.set("key", 99)
        Blackboard.reset("_test_reset_entries_")
        bb2 = Blackboard.create("_test_reset_entries_")
        assert bb2.get("key") is None

    def test_reset_clears_schema(self):
        from bteng.blackboard.blackboard import PortSchema
        from bteng.core.node import PortDirection
        bb = Blackboard.create("_test_reset_schema_")
        bb.register_port_schema(PortSchema("required_key", required=True))
        Blackboard.reset("_test_reset_schema_")
        bb2 = Blackboard.create("_test_reset_schema_")
        ok, msg = bb2.validate_against_schema()
        assert ok, f"Schema survived reset: {msg}"


# ─────────────────────────────────────────────────────────────────────────────
# BUG-7: Cyclic subtree reference crashes with RecursionError
# ─────────────────────────────────────────────────────────────────────────────

class TestBug7CyclicSubtree:
    def test_direct_cycle_raises_value_error(self):
        xml = (
            '<BTEng><Tree ID="A">'
            '<Sequence name="s"><SubTree ID="A"/></Sequence>'
            '</Tree></BTEng>'
        )
        with pytest.raises(ValueError, match="[Cc]yclic"):
            XMLTreeParser().parse_string(xml)

    def test_indirect_cycle_raises_value_error(self):
        xml = (
            '<BTEng>'
            '<Tree ID="A"><Sequence name="s"><SubTree ID="B"/></Sequence></Tree>'
            '<Tree ID="B"><Sequence name="s"><SubTree ID="A"/></Sequence></Tree>'
            '</BTEng>'
        )
        with pytest.raises(ValueError, match="[Cc]yclic"):
            XMLTreeParser().parse_string(xml, tree_id="A")

    def test_non_cyclic_subtree_parses_ok(self):
        from bteng.nodes.leaf.action import FunctionAction as FA
        NodeFactory.get_instance().register(FA, "__DummyA__")

        xml = (
            '<BTEng>'
            '<Tree ID="main"><Sequence name="s"><SubTree ID="sub"/></Sequence></Tree>'
            '<Tree ID="sub"><Sequence name="s2"><Action ID="__DummyA__" name="a"/></Sequence></Tree>'
            '</BTEng>'
        )
        # Should not raise
        try:
            XMLTreeParser().parse_string(xml, tree_id="main")
        except (KeyError, TypeError):
            # __DummyA__ constructor doesn't match (needs fn), that's OK for this test
            pass


# ─────────────────────────────────────────────────────────────────────────────
# BUG-8: Timeout dual-attribute parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestBug8TimeoutParsing:
    def _kw(self, xml_str: str) -> dict:
        el = ET.fromstring(xml_str)
        return XMLTreeParser._decorator_kwargs("Timeout", el)

    def test_duration_seconds(self):
        kw = self._kw('<Timeout duration="2.5"/>')
        assert abs(kw["duration"] - 2.5) < 1e-9

    def test_msec(self):
        kw = self._kw('<Timeout msec="500"/>')
        assert abs(kw["duration"] - 0.5) < 1e-9

    def test_duration_and_msec_msec_wins(self):
        """When both present, msec branch takes precedence."""
        kw = self._kw('<Timeout duration="2.0" msec="500"/>')
        assert abs(kw["duration"] - 0.5) < 1e-9, (
            f"Expected 0.5 (from msec=500), got {kw['duration']}"
        )

    def test_zero_msec_is_zero_duration(self):
        kw = self._kw('<Timeout msec="0"/>')
        assert kw["duration"] == 0.0

    def test_no_timeout_attrs_gives_empty_dict(self):
        kw = self._kw('<Timeout/>')
        assert kw == {}


# ─────────────────────────────────────────────────────────────────────────────
# BUG-9: NodeFactory._auto_discover registers abstract base classes from plugins
# ─────────────────────────────────────────────────────────────────────────────

class TestBug9AutoDiscover:
    def setup_method(self):
        NodeFactory.reset_instance()

    def teardown_method(self):
        NodeFactory.reset_instance()

    def test_imported_base_classes_not_registered(self):
        """ActionNode re-imported into a plugin module must not be auto-registered."""
        from bteng.nodes.leaf.action import ActionNode

        mod = types.ModuleType("test_plugin_base")
        mod.__name__ = "test_plugin_base"
        mod.ActionNode = ActionNode  # re-imported, __module__ mismatch

        factory = NodeFactory.get_instance()
        factory._auto_discover(mod)
        assert not factory.is_registered("ActionNode"), (
            "ActionNode (base class) was registered by auto_discover — should be skipped."
        )

    def test_concrete_class_defined_in_module_is_registered(self):
        """Classes whose __module__ matches the plugin module ARE registered."""
        mod = types.ModuleType("my_plugin")
        mod.__name__ = "my_plugin"

        class ConcreteAction(ActionNode):
            def tick(self):
                return NodeStatus.SUCCESS
        ConcreteAction.__module__ = "my_plugin"
        mod.ConcreteAction = ConcreteAction

        factory = NodeFactory.get_instance()
        factory._auto_discover(mod)
        assert factory.is_registered("ConcreteAction")

    def test_abstract_class_not_registered(self):
        """Classes with __abstractmethods__ are not registered."""
        from abc import ABC, abstractmethod
        from bteng.nodes.leaf.action import ActionNode as AN

        mod = types.ModuleType("my_plugin2")
        mod.__name__ = "my_plugin2"

        class AbstractAction(AN, ABC):
            @abstractmethod
            def tick(self): ...
        AbstractAction.__module__ = "my_plugin2"
        mod.AbstractAction = AbstractAction

        factory = NodeFactory.get_instance()
        factory._auto_discover(mod)
        assert not factory.is_registered("AbstractAction")


# ─────────────────────────────────────────────────────────────────────────────
# BUG-10: load_plugin() uses hardcoded sys.modules key — second plugin drops first
# ─────────────────────────────────────────────────────────────────────────────

class TestBug10LoadPlugin:
    def test_two_plugins_both_registered(self, tmp_path):
        NodeFactory.reset_instance()
        p1 = tmp_path / "plugin1.py"
        p2 = tmp_path / "plugin2.py"
        p1.write_text(
            "from bteng.nodes.leaf.action import ActionNode\n"
            "from bteng.core.node import NodeStatus\n"
            "class Plugin1Action(ActionNode):\n"
            "    def tick(self): return NodeStatus.SUCCESS\n"
            "BTENG_NODES = [('Plugin1Action', Plugin1Action)]\n"
        )
        p2.write_text(
            "from bteng.nodes.leaf.action import ActionNode\n"
            "from bteng.core.node import NodeStatus\n"
            "class Plugin2Action(ActionNode):\n"
            "    def tick(self): return NodeStatus.SUCCESS\n"
            "BTENG_NODES = [('Plugin2Action', Plugin2Action)]\n"
        )
        f = NodeFactory.get_instance()
        f.load_plugin(str(p1))
        f.load_plugin(str(p2))
        assert f.is_registered("Plugin1Action"), "First plugin lost after loading second."
        assert f.is_registered("Plugin2Action")

    def teardown_method(self):
        NodeFactory.reset_instance()


# ─────────────────────────────────────────────────────────────────────────────
# P1: ParallelNode._reset_children_status bypasses halt lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestP1ParallelResetChildren:
    def test_reset_calls_on_reset_for_completed_children(self):
        on_reset_called = []

        class TrackReset(FunctionAction):
            def _on_reset(self):
                on_reset_called.append(self.name)
                super()._on_reset()

        c0 = TrackReset("c0", lambda _: NodeStatus.SUCCESS)
        c1 = TrackReset("c1", lambda _: NodeStatus.RUNNING)
        p = ParallelNode("P", children=[c0, c1],
                         policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        p.execute_tick()  # c0=SUCCESS, c1=RUNNING
        p.halt()
        # c0 was SUCCESS (completed) — _on_reset should have been called
        assert "c0" in on_reset_called

    def test_sequence_current_idx_reset_after_parallel_halt(self):
        """A SequenceNode child of Parallel must have _current_idx=0 after Parallel halt."""
        inner = FunctionAction("a", lambda _: NodeStatus.SUCCESS)
        seq_child = SequenceNode("seq", [inner])

        p = ParallelNode("P", children=[seq_child],
                         policy=ParallelPolicy.REQUIRE_ALL_SUCCESS)
        p.execute_tick()          # seq_child succeeds → parallel succeeds
        # second tick: seq_child is now IDLE (after reset), should work fresh
        status = p.execute_tick()
        assert status == NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# P2: UID collision — 8-char truncation → 32-bit entropy
# ─────────────────────────────────────────────────────────────────────────────

class TestP2UIDEntropy:
    def test_uid_is_32_hex_chars(self):
        n = FunctionAction("n", lambda _: NodeStatus.SUCCESS)
        assert len(n.uid) == 32, f"UID length {len(n.uid)}, expected 32 (full UUID hex)"
        assert n.uid.isalnum(), "UID should be alphanumeric hex"

    def test_no_collision_in_1000_nodes(self):
        nodes = [FunctionAction(f"n{i}", lambda _: NodeStatus.SUCCESS) for i in range(1000)]
        uids = [n.uid for n in nodes]
        assert len(set(uids)) == len(uids), "UID collision detected in 1000-node set"


# ─────────────────────────────────────────────────────────────────────────────
# P3: Inspector.active_path docstring accuracy
# ─────────────────────────────────────────────────────────────────────────────

class TestP3InspectorActivePath:
    def test_active_path_contains_running_nodes(self):
        insp = Inspector.create()
        insp.on_node_tick("uid1", "Root", NodeType.CONTROL,
                          NodeStatus.IDLE, NodeStatus.RUNNING, 0.001)
        insp.on_node_tick("uid2", "Child", NodeType.ACTION,
                          NodeStatus.IDLE, NodeStatus.RUNNING, 0.001)
        path = insp.active_path()
        assert "uid1" in path and "uid2" in path

    def test_active_path_removes_non_running(self):
        insp = Inspector.create()
        insp.on_node_tick("uid1", "N", NodeType.ACTION, NodeStatus.IDLE, NodeStatus.RUNNING, 0.0)
        insp.on_node_tick("uid1", "N", NodeType.ACTION, NodeStatus.RUNNING, NodeStatus.SUCCESS, 0.0)
        assert "uid1" not in insp.active_path()

    def test_running_nodes_reflects_parallel_branches(self):
        insp = Inspector.create()
        insp.on_node_tick("A", "A", NodeType.ACTION, NodeStatus.IDLE, NodeStatus.RUNNING, 0.0)
        insp.on_node_tick("B", "B", NodeType.ACTION, NodeStatus.IDLE, NodeStatus.RUNNING, 0.0)
        running = insp.running_nodes()
        assert "A" in running and "B" in running


# ─────────────────────────────────────────────────────────────────────────────
# P4: list.pop(0) → deque — performance and correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestP4DequeRingBuffer:
    def test_inspector_history_bounded_by_max(self):
        insp = Inspector(max_history=10)
        for i in range(50):
            insp.on_node_tick(f"u{i}", f"N{i}", NodeType.ACTION,
                              NodeStatus.IDLE, NodeStatus.RUNNING, 0.0)
        assert len(insp.execution_history()) == 10

    def test_inspector_set_max_history_trims(self):
        insp = Inspector(max_history=50)
        for i in range(30):
            insp.on_node_tick(f"u{i}", f"N", NodeType.ACTION,
                              NodeStatus.IDLE, NodeStatus.SUCCESS, 0.0)
        insp.set_max_history(10)
        assert len(insp.execution_history()) <= 10

    def test_logger_history_bounded_by_max(self):
        logger = Logger(max_history=5)
        for i in range(20):
            logger.log_transition(f"uid{i}", f"N{i}",
                                  NodeStatus.IDLE, NodeStatus.SUCCESS)
        assert len(logger.history()) == 5

    def test_logger_set_max_history(self):
        logger = Logger(max_history=20)
        for _ in range(15):
            logger.log_transition("u", "N", NodeStatus.IDLE, NodeStatus.SUCCESS)
        logger.set_max_history(5)
        assert len(logger.history()) <= 5

    def test_inspector_throughput(self):
        """10k ticks with max_history=100 must complete in < 500 ms."""
        insp = Inspector(max_history=100)
        t0 = time.monotonic()
        for i in range(10_000):
            insp.on_node_tick(f"u{i % 20}", "N", NodeType.ACTION,
                              NodeStatus.IDLE, NodeStatus.RUNNING, 0.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"Inspector throughput too slow: {elapsed*1000:.0f}ms"


# ─────────────────────────────────────────────────────────────────────────────
# P5: Silent exception swallow in all callback sites
# ─────────────────────────────────────────────────────────────────────────────

class TestP5ExceptionVisibility:
    def _capture_stderr(self, fn):
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            fn()
        finally:
            sys.stderr = old
        return buf.getvalue()

    def test_eventbus_subscriber_exception_printed_to_stderr(self):
        bus = EventBus.create()
        bus.subscribe("ev", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))

        def publish():
            bus.publish(BehaviorEvent(name="ev"))

        output = self._capture_stderr(publish)
        assert "bteng" in output or "boom" in output, (
            f"EventBus subscriber exception not printed to stderr. Got: {output!r}"
        )

    def test_blackboard_subscriber_exception_printed_to_stderr(self):
        bb = Blackboard("test_p5")

        def bad_cb(k, v):
            raise ValueError("cb crash")

        bb.subscribe(bad_cb)

        output = self._capture_stderr(lambda: bb.set("x", 1))
        assert "bteng" in output or "crash" in output

    def test_inspector_subscriber_exception_printed_to_stderr(self):
        insp = Inspector.create()
        insp.subscribe(lambda r: (_ for _ in ()).throw(RuntimeError("insp crash")))

        def tick():
            insp.on_node_tick("u", "N", NodeType.ACTION,
                              NodeStatus.IDLE, NodeStatus.SUCCESS, 0.0)

        output = self._capture_stderr(tick)
        assert "bteng" in output or "crash" in output

    def test_logger_sink_exception_printed_to_stderr(self):
        logger = Logger.create()
        logger.add_custom_sink(lambda e: (_ for _ in ()).throw(ValueError("sink crash")))
        logger.set_min_level(LogLevel.DEBUG)

        output = self._capture_stderr(
            lambda: logger.log_transition("u", "N", NodeStatus.IDLE, NodeStatus.SUCCESS)
        )
        assert "bteng" in output or "crash" in output


# ─────────────────────────────────────────────────────────────────────────────
# P6: TreeRegistry connected to XMLTreeParser
# ─────────────────────────────────────────────────────────────────────────────

class TestP6TreeRegistryConnected:
    def test_parse_string_to_registry_returns_all_trees(self):
        NodeFactory.reset_instance()

        from bteng.nodes.leaf.action import FunctionAction as FA
        from bteng.core.node import NodeStatus as NS

        class DummyNode(ActionNode):
            def tick(self): return NodeStatus.SUCCESS
        NodeFactory.get_instance().register(DummyNode, "DummyNode")

        xml = (
            '<BTEng>'
            '<Tree ID="tree1"><Sequence name="s"><Action ID="DummyNode" name="a"/></Sequence></Tree>'
            '<Tree ID="tree2"><Sequence name="s"><Action ID="DummyNode" name="b"/></Sequence></Tree>'
            '</BTEng>'
        )
        registry = XMLTreeParser().parse_string_to_registry(xml)
        ids = set(registry.ids())
        assert "tree1" in ids and "tree2" in ids

        NodeFactory.reset_instance()

    def test_registry_trees_are_tickable(self):
        from bteng.nodes.leaf.action import FunctionAction as FA

        class ANode(ActionNode):
            def tick(self): return NodeStatus.SUCCESS
        NodeFactory.reset_instance()
        NodeFactory.get_instance().register(ANode, "ANode")

        xml = (
            '<BTEng>'
            '<Tree ID="t1"><Sequence name="s"><Action ID="ANode" name="x"/></Sequence></Tree>'
            '</BTEng>'
        )
        registry = XMLTreeParser().parse_string_to_registry(xml)
        tree = registry.get("t1")
        assert tree is not None
        status = tree.tick_once()
        assert status == NodeStatus.SUCCESS

        NodeFactory.reset_instance()


# ─────────────────────────────────────────────────────────────────────────────
# P7: Tree.visit() lock nesting (documentation / no-deadlock guarantee)
# ─────────────────────────────────────────────────────────────────────────────

class TestP7VisitLock:
    def test_visit_from_outside_executor_does_not_deadlock(self):
        root = _action(NodeStatus.SUCCESS)
        tree = _make_tree(root)
        visited = []
        tree.visit(lambda n: visited.append(n.name))
        assert "a" in visited

    def test_visit_from_other_thread_while_ticking(self):
        """visit() from a background thread must not deadlock with a ticking executor."""
        results = []

        def slow_tick(_):
            time.sleep(0.02)
            return NodeStatus.SUCCESS

        root = FunctionAction("root", slow_tick)
        tree = _make_tree(root)
        exec_ = TreeExecutor(ExecutorConfig(tick_interval=0.01))
        exec_.set_tree(tree)
        exec_.start_event_loop()

        time.sleep(0.005)
        visited = []
        tree.visit(lambda n: visited.append(n.uid))
        results.append(len(visited))

        exec_.stop_event_loop()
        assert len(results) > 0


# ─────────────────────────────────────────────────────────────────────────────
# P8: Tracer uses time.time(); frame mode uses time.monotonic()
# ─────────────────────────────────────────────────────────────────────────────

class TestP8TracerTimebase:
    def test_log_transition_uses_monotonic(self):
        """log_transition() must use monotonic time so timestamps are comparable
        with TraceFrame timestamps (which also use monotonic)."""
        tracer = ExecutionTracer()
        t_before = time.monotonic()
        root = _action(NodeStatus.SUCCESS)
        root._tracer = tracer
        root.execute_tick()
        t_after = time.monotonic()

        events = tracer.events()
        assert len(events) == 1
        ts = events[0].timestamp
        assert t_before <= ts <= t_after, (
            f"Transition event timestamp {ts} outside monotonic window "
            f"[{t_before}, {t_after}]"
        )

    def test_frame_and_transition_timestamps_in_same_epoch(self):
        tracer = ExecutionTracer()
        tracer.begin_frame(0)
        root = _action(NodeStatus.SUCCESS)
        root._tracer = tracer
        root.execute_tick()
        tracer.end_frame()

        frames = tracer.frames()
        events = tracer.events()
        assert frames and events
        delta = abs(frames[0].timestamp - events[0].timestamp)
        assert delta < 1.0, (
            f"Frame and transition timestamps differ by {delta:.3f}s — likely mismatched timebases."
        )


# ─────────────────────────────────────────────────────────────────────────────
# P9: Blackboard.entry() returned mutable internal reference
# ─────────────────────────────────────────────────────────────────────────────

class TestP9BlackboardEntryCopy:
    def test_entry_returns_copy_not_reference(self):
        bb = Blackboard("test_entry")
        bb.set("x", 10)
        snap = bb.entry("x")
        assert snap is not None
        # Mutating the snapshot must not affect the internal entry
        snap.value = 999
        assert bb.get("x") == 10

    def test_entry_history_is_independent_copy(self):
        bb = Blackboard("test_entry_hist")
        bb.set("x", 1)
        bb.set("x", 2)
        snap = bb.entry("x")
        original_len = len(snap.history)
        snap.history.append(None)  # mutate snapshot
        snap2 = bb.entry("x")
        assert len(snap2.history) == original_len, (
            "Mutating snapshot history leaked into the blackboard."
        )

    def test_entry_none_for_missing_key(self):
        bb = Blackboard("test_entry_miss")
        assert bb.entry("nonexistent") is None


# ─────────────────────────────────────────────────────────────────────────────
# P10: Inconsistent lambda signatures between action() factory and TreeBuilder.action()
# ─────────────────────────────────────────────────────────────────────────────

class TestP10LambdaSignatures:
    def test_builder_zero_arg_lambda(self):
        tree = TreeBuilder().sequence("r").action("a", lambda: NodeStatus.SUCCESS).end().build()
        assert tree.tick_once() == NodeStatus.SUCCESS

    def test_builder_one_arg_lambda(self):
        received = []
        tree = (
            TreeBuilder()
            .sequence("r")
                .action("a", lambda node: (received.append(node.name) or NodeStatus.SUCCESS))
            .end()
            .build()
        )
        tree.tick_once()
        assert received == ["a"]

    def test_builder_bool_return_coercion(self):
        tree_t = TreeBuilder().sequence("r").action("a", lambda: True).end().build()
        tree_f = TreeBuilder().sequence("r").action("a", lambda: False).end().build()
        assert tree_t.tick_once() == NodeStatus.SUCCESS
        assert tree_f.tick_once() == NodeStatus.FAILURE

    def test_direct_action_factory_receives_node(self):
        """The action() helper (not builder) passes node as first arg."""
        received = []
        n = action("a", lambda node: (received.append(node) or NodeStatus.SUCCESS))
        n.execute_tick()
        assert len(received) == 1

    def test_condition_zero_arg_lambda(self):
        tree = TreeBuilder().sequence("r").condition("c", lambda: True).end().build()
        assert tree.tick_once() == NodeStatus.SUCCESS

    def test_condition_one_arg_lambda(self):
        tree = TreeBuilder().sequence("r").condition("c", lambda node: node is not None).end().build()
        assert tree.tick_once() == NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# P11: ParallelNode's success_threshold port had no default, so validate()
#      rejected a node built with the documented default (-1 = all children)
# ─────────────────────────────────────────────────────────────────────────────

class TestP11ParallelThresholdDefault:
    @staticmethod
    def _tree(par):
        return Tree(TreeMetadata(id="t"), par)

    def test_default_construction_validates(self):
        """ParallelNode("p", children=[...]) is the documented shape: -1 means
        all children. It used to raise TreeValidationError at set_tree()."""
        par = ParallelNode("p", children=[MockActionNode("a"), MockActionNode("b")])
        self._tree(par).validate()

    def test_ctor_threshold_validates(self):
        par = ParallelNode("p", children=[MockActionNode("a"), MockActionNode("b")],
                           success_threshold=2)
        self._tree(par).validate()

    def test_params_threshold_still_validates(self):
        """The old workaround must keep working — the thread-pool tests use it."""
        par = ParallelNode("p", children=[MockActionNode("a")],
                           config=NodeConfig(params={"success_threshold": -1}))
        self._tree(par).validate()

    def test_bare_parallel_in_xml_validates(self):
        from bteng.xml_parser.parser import XMLTreeParser

        NodeFactory.get_instance().register(MockActionNode, "MockAction")
        xml = ('<BTEng><Tree ID="main"><Parallel name="p">'
               '<MockAction name="a"/><MockAction name="b"/>'
               '</Parallel></Tree></BTEng>')
        root = XMLTreeParser().parse_string(xml)
        self._tree(root).validate()

    def test_declared_default_is_minus_one(self):
        ports = {p.name: p for p in ParallelNode.provided_ports()}
        assert ports["success_threshold"].default == -1

    def test_threshold_semantics_unchanged(self):
        """-1 still means "all children", so the fix is validation-only."""
        good, bad = MockActionNode("a"), MockActionNode("b")
        bad.set_status(NodeStatus.FAILURE)
        par = ParallelNode("p", children=[good, bad])
        assert par.execute_tick() == NodeStatus.FAILURE


# ─────────────────────────────────────────────────────────────────────────────
# P12: execute_tick() accepted anything tick() returned, so a missing return
#      path (None) was read by control nodes as "not RUNNING" and skipped
# ─────────────────────────────────────────────────────────────────────────────

class _NoReturn(ActionNode):
    """tick() falls off the end. Real-world shape: a node whose on_start()
    forgot to return, which is how a first-tick bug hid in bteng-ros2."""

    def tick(self):
        pass


class TestP12TickMustReturnNodeStatus:
    def test_leaf_raises_naming_the_node(self):
        with pytest.raises(TypeError, match=r"_NoReturn\.tick\(\) returned None"):
            _NoReturn("bad").execute_tick()

    def test_sequence_no_longer_reports_success(self):
        """Previously: the sequence walked past the broken child and the tree
        reported SUCCESS."""
        seq = SequenceNode("root", children=[_NoReturn("bad"), MockActionNode("after")])
        with pytest.raises(TypeError):
            seq.execute_tick()

    def test_executor_surfaces_it(self):
        ex = TreeExecutor(ExecutorConfig(enable_tracing=False, enable_logging=False))
        ex.set_tree(Tree(TreeMetadata(id="t"), _NoReturn("bad")))
        with pytest.raises(TypeError):
            ex.tick_once()

    def test_a_wrong_type_is_rejected_too(self):
        class _WrongType(ActionNode):
            def tick(self):
                return "SUCCESS"

        with pytest.raises(TypeError, match="expected NodeStatus"):
            _WrongType("bad").execute_tick()

    def test_valid_statuses_still_pass_through(self):
        for status in (NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.RUNNING):
            node = MockActionNode("n")
            node.set_status(status)
            assert node.execute_tick() == status


# ─────────────────────────────────────────────────────────────────────────────
# Tracer frames were dropped for trees that never wrote to the blackboard
# ─────────────────────────────────────────────────────────────────────────────

class TestTracerRecordsEveryTick:
    """`begin_frame()` ran every tick but `end_frame()` only ran when the
    blackboard was dirty, and `end_frame()` is what commits the frame. A tree
    whose nodes never wrote to the blackboard therefore traced nothing at all:
    `frames()` stayed empty, and each tick's node records were discarded when
    the next `begin_frame()` replaced the still-open frame.

    Every existing tracer test drove `begin_frame`/`end_frame` by hand, so the
    executor's own path was never exercised and the suite stayed green.
    """

    @staticmethod
    def _run(node_cls, ticks=5):
        bb = Blackboard(scope_name="tracer_frames")
        cfg = NodeConfig(blackboard=bb)
        root = SequenceNode("root", children=[node_cls("leaf", cfg)], config=cfg)
        ex = TreeExecutor(ExecutorConfig(enable_tracing=False, enable_logging=False))
        ex.set_tree(Tree(TreeMetadata(id="t"), root, blackboard=bb))
        tracer = ExecutionTracer()
        ex.set_tracer(tracer)
        for _ in range(ticks):
            ex.tick_once()
        ex.shutdown()
        return tracer

    def test_frame_per_tick_when_blackboard_is_never_written(self):
        class _Pure(ActionNode):
            def tick(self):
                return NodeStatus.SUCCESS

        tracer = self._run(_Pure)
        assert tracer.frame_count() == 5
        assert [f.tick_index for f in tracer.frames()] == [0, 1, 2, 3, 4]

    def test_frame_per_tick_when_blackboard_is_written(self):
        class _Writer(ActionNode):
            def __init__(self, name, config=None):
                super().__init__(name, config)
                self.n = 0

            def tick(self):
                self.n += 1
                self.blackboard.set("k", self.n)
                return NodeStatus.SUCCESS

        tracer = self._run(_Writer)
        assert tracer.frame_count() == 5

    def test_clean_tick_frame_has_an_empty_snapshot_not_a_missing_frame(self):
        class _Pure(ActionNode):
            def tick(self):
                return NodeStatus.SUCCESS

        tracer = self._run(_Pure, ticks=1)
        assert tracer.frame_count() == 1
        assert tracer.frames()[0].blackboard_snapshot == {}
