"""Regression tests for the TreeExecutor / BehaviorTreeEngine fixes.

- Nodes installed by a runtime modification now receive setup() and the
  tracer/inspector/thread-pool injections the rest of the tree already had.
- tick_until_result() records final_status, like the event-loop path.
- BehaviorTreeEngine(blackboard=...) actually wires the blackboard into the tree.
"""
from __future__ import annotations

from bteng.blackboard.blackboard import Blackboard
from bteng.core.engine import BehaviorTreeEngine
from bteng.core.executor import ExecutorConfig, TreeExecutor
from bteng.core.node import NodeConfig, NodeStatus, TreeNode
from bteng.core.tree import ModificationType, Tree, TreeMetadata, TreeModification
from bteng.logging.tracer import ExecutionTracer
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.leaf.action import ActionNode


class _Recorder(ActionNode):
    """Records whether setup()/shutdown() ran, and what got injected."""

    def __init__(self, name: str, result: NodeStatus = NodeStatus.SUCCESS, **kw) -> None:
        super().__init__(name, **kw)
        self.result = result
        self.setup_calls = 0
        self.shutdown_calls = 0

    def setup(self) -> None:
        self.setup_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def tick(self) -> NodeStatus:
        return self.result


class _Running(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.RUNNING


def _executor(tree, **cfg) -> TreeExecutor:
    ex = TreeExecutor(ExecutorConfig(enable_tracing=False, enable_logging=False, **cfg))
    ex.set_tree(tree)
    return ex


def _tree(root: TreeNode) -> Tree:
    return Tree(TreeMetadata(id="t"), root)


# ── nodes added by a modification ───────────────────────────────────────────────

def test_a_node_added_by_a_modification_gets_setup():
    keep = _Recorder("keep", NodeStatus.RUNNING)
    root = SequenceNode("root", children=[keep])
    tree = _tree(root)
    ex = _executor(tree)
    ex.tick_once()
    assert keep.setup_calls == 1

    late = _Recorder("late")
    tree.queue_modification(TreeModification(
        type=ModificationType.INSERT_CHILD, target_uid=root.uid, new_node=late, child_index=0,
    ))
    ex.tick_once()
    assert late.setup_calls == 1, "hot-swapped node ticked without setup()"


def test_setup_is_not_run_twice_on_existing_nodes():
    keep = _Recorder("keep", NodeStatus.RUNNING)
    root = SequenceNode("root", children=[keep])
    tree = _tree(root)
    ex = _executor(tree)
    ex.tick_once()
    tree.queue_modification(TreeModification(
        type=ModificationType.INSERT_CHILD, target_uid=root.uid,
        new_node=_Recorder("late"), child_index=0,
    ))
    ex.tick_once()
    ex.tick_once()
    assert keep.setup_calls == 1


def test_a_node_added_by_a_modification_gets_the_tracer():
    keep = _Recorder("keep", NodeStatus.RUNNING)
    root = SequenceNode("root", children=[keep])
    tree = _tree(root)
    ex = TreeExecutor(ExecutorConfig(enable_logging=False))
    tracer = ExecutionTracer()
    ex.set_tracer(tracer)
    ex.set_tree(tree)
    ex.tick_once()

    late = _Recorder("late")
    tree.queue_modification(TreeModification(
        type=ModificationType.INSERT_CHILD, target_uid=root.uid, new_node=late, child_index=0,
    ))
    ex.tick_once()
    assert late._tracer is tracer


def test_shutdown_lets_a_later_run_set_up_again():
    node = _Recorder("n")
    ex = _executor(_tree(node))
    ex.tick_once()
    ex.shutdown()
    assert node.shutdown_calls == 1
    ex.tick_once()
    assert node.setup_calls == 2, "setup() must run again after a shutdown()"


# ── final_status on the manual path ─────────────────────────────────────────────

def test_tick_until_result_records_final_status():
    ex = _executor(_tree(_Recorder("n")))
    assert ex.final_status is None
    assert ex.tick_until_result(max_ticks=5) == NodeStatus.SUCCESS
    assert ex.final_status == NodeStatus.SUCCESS


def test_final_status_reflects_a_failure():
    ex = _executor(_tree(_Recorder("n", NodeStatus.FAILURE)))
    ex.tick_until_result(max_ticks=5)
    assert ex.final_status == NodeStatus.FAILURE


def test_final_status_stays_none_while_running():
    ex = _executor(_tree(_Running("n")))
    ex.tick_until_result(max_ticks=3)
    assert ex.final_status is None


# ── engine blackboard wiring ────────────────────────────────────────────────────

class _NeedsBlackboard(ActionNode):
    @classmethod
    def provided_ports(cls):
        from bteng.core.node import InputPort, OutputPort
        return [InputPort("target"), OutputPort("seen")]

    def tick(self) -> NodeStatus:
        target = self.get_input("target")
        if target is None:
            return NodeStatus.FAILURE
        self.set_output("seen", target)
        return NodeStatus.SUCCESS


def test_engine_wires_its_blackboard_into_the_tree():
    bb = Blackboard(scope_name="engine_test")
    bb.set("goal", "kitchen")
    node = _NeedsBlackboard("n", config=NodeConfig(
        input_ports={"target": "goal"}, output_ports={"seen": "reached"},
    ))
    engine = BehaviorTreeEngine(node, blackboard=bb)
    assert engine.tick_once() == NodeStatus.SUCCESS
    assert bb.get("reached") == "kitchen"


def test_engine_does_not_overwrite_a_node_s_own_blackboard():
    own = Blackboard(scope_name="own")
    own.set("goal", "own value")
    other = Blackboard(scope_name="other")
    other.set("goal", "other value")

    node = _NeedsBlackboard("n", config=NodeConfig(
        blackboard=own, input_ports={"target": "goal"}, output_ports={"seen": "reached"},
    ))
    BehaviorTreeEngine(node, blackboard=other).tick_once()
    assert own.get("reached") == "own value"
    assert other.get("reached") is None


def test_engine_reaches_nested_nodes():
    bb = Blackboard(scope_name="engine_nested")
    bb.set("goal", 7)
    leaf = _NeedsBlackboard("leaf", config=NodeConfig(
        input_ports={"target": "goal"}, output_ports={"seen": "reached"},
    ))
    engine = BehaviorTreeEngine(SequenceNode("root", children=[leaf]), blackboard=bb)
    assert engine.tick_once() == NodeStatus.SUCCESS
    assert bb.get("reached") == 7
