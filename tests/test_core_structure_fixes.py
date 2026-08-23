"""Regression tests for the core structure fixes (node / tree / tree_builder / subtree).

Covered findings:
  #1   DecoratorNode.get_children() returned a stale cached snapshot.
  #2   Runtime modifications corrupted control-node cursors.
  #8   SubTree.halt() dropped _on_halt().
  #10  TreeNode.halt() never told the Inspector; unnamed trees shared a Blackboard.
  #13  NodeStatus.IDLE slipped past the execute_tick() return-type guard.
  #15  _apply_modification() accepted garbage silently.
  #16  tip() is RUNNING-only, contrary to its old docstrings.
"""
from __future__ import annotations

import logging

import pytest

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import (
    ControlNode, DecoratorNode, LeafNode, NodeConfig, NodeStatus, TreeNode,
)
from bteng.core.executor import ExecutorConfig, TreeExecutor
from bteng.core.tree import (
    ModificationType, Tree, TreeMetadata, TreeModification,
)
from bteng.core.tree_builder import TreeBuilder
from bteng.introspection.inspector import Inspector
from bteng.nodes.control.fallback import FallbackNode
from bteng.nodes.control.reactive_sequence import ReactiveSequenceNode
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.decorators.inverter import Inverter
from bteng.nodes.leaf.action import ActionNode, FunctionAction
from bteng.nodes.subtree import SubTree

R, S, F, IDLE = (
    NodeStatus.RUNNING, NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.IDLE,
)

TREE_LOGGER = "bteng.core.tree"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tree(root: TreeNode, id_: str = "test") -> Tree:
    return Tree(TreeMetadata(id=id_), root)


def _scripted(name: str, log: list, statuses) -> FunctionAction:
    """An action that returns `statuses` in order, repeating the last one."""
    remaining = list(statuses)
    last = [remaining[0]]

    def fn(_node):
        log.append(name)
        if remaining:
            last[0] = remaining.pop(0)
        return last[0]

    return FunctionAction(name, fn)


class _Probe(ActionNode):
    """Leaf that records every lifecycle callback it receives."""

    def __init__(self, name: str, calls: list, result: NodeStatus = S):
        super().__init__(name, NodeConfig())
        self._calls = calls
        self._result = result

    def setup(self) -> None:
        self._calls.append(("setup", self.name))

    def shutdown(self) -> None:
        self._calls.append(("shutdown", self.name))

    def _on_reset(self) -> None:
        self._calls.append(("reset", self.name))

    def tick(self) -> NodeStatus:
        return self._result


# ─────────────────────────────────────────────────────────────────────────────
# #1 — DecoratorNode.get_children() must follow _child, not a cached list
# ─────────────────────────────────────────────────────────────────────────────

class TestDecoratorChildrenAreLive:
    def test_builder_decorator_child_is_reachable(self):
        """TreeBuilder overwrites the placeholder _child; walks must see the real one."""
        calls: list = []
        b = TreeBuilder()
        b.tree_id("builder_decorator")
        b.sequence("root")
        b.inverter("inv")
        b._attach_leaf(_Probe("under_inverter", calls))
        b.end()
        b._attach_leaf(_Probe("plain", calls))
        b.end()
        tree = b.build()

        inv = tree.root.get_children()[0]
        assert [c.name for c in inv.get_children()] == ["under_inverter"]
        assert tree.find_node_by_name("under_inverter") is inv._child
        assert tree.find_node(inv._child.uid) is inv._child

        seen: list = []
        tree.visit(lambda n: seen.append(n.name))
        assert seen == ["root", "inv", "under_inverter", "plain"]
        assert "__placeholder__" not in tree.ascii_tree()

    def test_builder_decorator_child_receives_setup_and_shutdown(self):
        """The most confusing failure mode: the node ticks but setup() never ran."""
        calls: list = []
        b = TreeBuilder()
        b.tree_id("builder_decorator_setup")
        b.sequence("root")
        b.inverter("inv")
        b._attach_leaf(_Probe("under_inverter", calls))
        b.end()
        b.end()
        tree = b.build()

        ex = TreeExecutor(ExecutorConfig(enable_tracing=False))
        ex.set_tree(tree)
        ex.tick_once()
        ex.shutdown()

        assert ("setup", "under_inverter") in calls
        assert ("shutdown", "under_inverter") in calls

    def test_replace_node_under_decorator_is_reachable(self):
        calls: list = []
        old = _Probe("old", calls)
        new = _Probe("new", calls)
        inv = Inverter("inv", old)
        tree = _tree(inv, "replace_under_decorator")

        tree.queue_modification(TreeModification(
            type=ModificationType.REPLACE_NODE, target_uid=old.uid, new_node=new,
        ))
        tree.apply_pending_modifications()

        assert inv._child is new
        assert [c.name for c in inv.get_children()] == ["new"]
        assert tree.find_node_by_name("new") is new
        assert tree.find_node_by_name("old") is None
        assert tree.find_node(new.uid) is new

        seen: list = []
        tree.visit(lambda n: seen.append(n.name))
        assert seen == ["inv", "new"]

        # reset_all() recurses through get_children(): it must reach the new child.
        calls.clear()
        tree.reset_all()
        assert ("reset", "new") in calls
        assert ("reset", "old") not in calls

    def test_get_children_is_a_copy(self):
        """Callers must not be able to reshape the tree through get_children()."""
        child = FunctionAction("c", lambda _: S)
        inv = Inverter("inv", child)
        children = inv.get_children()
        children.append(FunctionAction("intruder", lambda _: S))
        assert [c.name for c in inv.get_children()] == ["c"]


# ─────────────────────────────────────────────────────────────────────────────
# #2 — runtime modifications must not corrupt control-node cursors
# ─────────────────────────────────────────────────────────────────────────────

class TestModificationsKeepCursorsConsistent:
    def test_remove_child_does_not_make_sequence_report_success(self):
        log: list = []
        a = _scripted("A", log, [S])
        b = _scripted("B", log, [R, R, S])
        seq = SequenceNode("seq", [a, b])
        tree = _tree(seq, "m1")

        assert tree.tick_once() is R
        assert log == ["A", "B"]

        tree.queue_modification(TreeModification(
            type=ModificationType.REMOVE_CHILD, target_uid=seq.uid, child_index=0,
        ))
        status = tree.tick_once()

        # 'B' has not finished, so a final result would be a lie.
        assert status is R
        assert log == ["A", "B", "B"]
        assert [c.name for c in seq.get_children()] == ["B"]

    def test_remove_child_does_not_make_fallback_report_failure(self):
        log: list = []
        fb = FallbackNode("fb", [
            _scripted("A", log, [F]),
            _scripted("B", log, [R, R, S]),
        ])
        tree = _tree(fb, "m3")

        assert tree.tick_once() is R
        tree.queue_modification(TreeModification(
            type=ModificationType.REMOVE_CHILD, target_uid=fb.uid, child_index=0,
        ))
        assert tree.tick_once() is R
        assert log == ["A", "B", "B"]

    def test_insert_child_does_not_skip_the_new_child(self):
        log: list = []
        seq = SequenceNode("seq", [
            _scripted("A", log, [S]),
            _scripted("B", log, [R, S]),
        ])
        tree = _tree(seq, "m2")

        assert tree.tick_once() is R
        assert log == ["A", "B"]

        tree.queue_modification(TreeModification(
            type=ModificationType.INSERT_CHILD, target_uid=seq.uid, child_index=0,
            new_node=_scripted("NEW", log, [S]),
        ))
        tree.tick_once()

        assert "NEW" in log, "the inserted child was skipped by a stale cursor"
        assert log.index("NEW") == 2, "the branch must restart at the new first child"

    def test_remove_child_leaves_reactive_sequence_tickable(self):
        """The fast path used to hold an index past the end and raise IndexError."""
        bb = Blackboard(scope_name="m4")
        cfg = NodeConfig(blackboard=bb)
        rs = ReactiveSequenceNode("rs", [
            FunctionAction("cond", lambda _: S, config=cfg),
            FunctionAction("run", lambda _: R, config=cfg),
        ], config=cfg)
        tree = _tree(rs, "m4")

        tree.tick_once()
        tree.tick_once()
        tree.queue_modification(TreeModification(
            type=ModificationType.REMOVE_CHILD, target_uid=rs.uid, child_index=1,
        ))
        assert tree.tick_once() in (R, S, F)   # notably: does not raise IndexError

    def test_halted_parent_children_are_halted_too(self):
        """A removed sibling's replacement branch restarts from a clean state."""
        seq = SequenceNode("seq", [
            FunctionAction("a", lambda _: S),
            FunctionAction("b", lambda _: R),
        ])
        tree = _tree(seq, "m5")
        tree.tick_once()
        assert seq._current_idx == 1

        tree.queue_modification(TreeModification(
            type=ModificationType.INSERT_CHILD, target_uid=seq.uid, child_index=2,
            new_node=FunctionAction("c", lambda _: S),
        ))
        tree.apply_pending_modifications()
        assert seq._current_idx == 0
        assert seq.status is IDLE
        assert all(c.status is IDLE for c in seq.get_children())


# ─────────────────────────────────────────────────────────────────────────────
# #15 — _apply_modification() must not accept garbage silently
# ─────────────────────────────────────────────────────────────────────────────

class TestModificationValidation:
    @pytest.mark.parametrize("mod_type", [
        ModificationType.INSERT_CHILD,
        ModificationType.REPLACE_NODE,
        ModificationType.HOT_SWAP_SUBTREE,
    ])
    def test_missing_new_node_is_rejected(self, mod_type):
        root = FunctionAction("root", lambda _: S)
        tree = _tree(root, "no_new_node")
        with pytest.raises(ValueError, match="new_node"):
            tree.queue_modification(TreeModification(
                type=mod_type, target_uid=root.uid, new_node=None,
            ))

    def test_replace_root_with_none_does_not_poison_the_tree(self):
        root = FunctionAction("root", lambda _: S)
        tree = _tree(root, "poison")
        with pytest.raises(ValueError):
            tree.queue_modification(TreeModification(
                type=ModificationType.REPLACE_NODE, target_uid=root.uid,
            ))
        # Root untouched and the tree still ticks.
        assert tree.root is root
        assert tree.tick_once() is S

    def test_remove_child_needs_no_new_node(self):
        seq = SequenceNode("seq", [FunctionAction("a", lambda _: S)])
        tree = _tree(seq, "remove_ok")
        tree.queue_modification(TreeModification(
            type=ModificationType.REMOVE_CHILD, target_uid=seq.uid, child_index=0,
        ))
        tree.apply_pending_modifications()
        assert seq.get_children() == []

    def test_missing_target_uid_is_rejected(self):
        seq = SequenceNode("seq", [FunctionAction("a", lambda _: S)])
        tree = _tree(seq, "no_target")
        with pytest.raises(ValueError, match="target_uid"):
            tree.queue_modification(TreeModification(
                type=ModificationType.REMOVE_CHILD, target_uid="",
            ))

    def test_non_modification_is_rejected(self):
        tree = _tree(FunctionAction("root", lambda _: S), "wrong_type")
        with pytest.raises(TypeError):
            tree.queue_modification("REPLACE_NODE")  # type: ignore[arg-type]

    def test_unknown_target_is_logged(self, caplog):
        seq = SequenceNode("seq", [FunctionAction("a", lambda _: S)])
        tree = _tree(seq, "unknown_target")
        tree.queue_modification(TreeModification(
            type=ModificationType.REPLACE_NODE, target_uid="deadbeef",
            new_node=FunctionAction("new", lambda _: S),
        ))
        with caplog.at_level(logging.WARNING, logger=TREE_LOGGER):
            tree.apply_pending_modifications()
        assert "deadbeef" in caplog.text
        assert "not in this tree" in caplog.text

    def test_unknown_insert_target_is_logged(self, caplog):
        seq = SequenceNode("seq", [FunctionAction("a", lambda _: S)])
        tree = _tree(seq, "unknown_insert")
        tree.queue_modification(TreeModification(
            type=ModificationType.INSERT_CHILD, target_uid="nosuchuid",
            new_node=FunctionAction("new", lambda _: S),
        ))
        with caplog.at_level(logging.WARNING, logger=TREE_LOGGER):
            tree.apply_pending_modifications()
        assert "nosuchuid" in caplog.text

    def test_out_of_range_remove_index_is_logged(self, caplog):
        seq = SequenceNode("seq", [FunctionAction("a", lambda _: S)])
        tree = _tree(seq, "bad_index")
        tree.queue_modification(TreeModification(
            type=ModificationType.REMOVE_CHILD, target_uid=seq.uid, child_index=7,
        ))
        with caplog.at_level(logging.WARNING, logger=TREE_LOGGER):
            tree.apply_pending_modifications()
        assert "out of range" in caplog.text
        assert len(seq.get_children()) == 1

    def test_insert_child_on_decorator_replaces_its_only_child(self, caplog):
        old = FunctionAction("old", lambda _: S)
        inv = Inverter("inv", old)
        tree = _tree(inv, "decorator_insert")
        new = FunctionAction("new", lambda _: S)
        tree.queue_modification(TreeModification(
            type=ModificationType.INSERT_CHILD, target_uid=inv.uid, new_node=new,
        ))
        with caplog.at_level(logging.WARNING, logger=TREE_LOGGER):
            tree.apply_pending_modifications()
        assert inv._child is new
        assert "decorator" in caplog.text

    def test_remove_child_on_decorator_is_refused(self, caplog):
        child = FunctionAction("c", lambda _: S)
        inv = Inverter("inv", child)
        tree = _tree(inv, "decorator_remove")
        tree.queue_modification(TreeModification(
            type=ModificationType.REMOVE_CHILD, target_uid=inv.uid, child_index=0,
        ))
        with caplog.at_level(logging.WARNING, logger=TREE_LOGGER):
            tree.apply_pending_modifications()
        # Refused, so the decorator still has a child and still ticks.
        assert inv._child is child
        assert "refused" in caplog.text
        assert tree.tick_once() is F   # Inverter of SUCCESS

    def test_hot_swap_subtree_rejects_none(self):
        root = FunctionAction("root", lambda _: S)
        tree = _tree(root, "hotswap_none")
        with pytest.raises(ValueError):
            tree.hot_swap_subtree(root.uid, None)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# #13 — NodeStatus.IDLE is not a valid tick() result
# ─────────────────────────────────────────────────────────────────────────────

class TestIdleIsNotAResult:
    def test_idle_tick_raises(self):
        node = FunctionAction("idle", lambda _: IDLE)
        with pytest.raises(TypeError, match="IDLE"):
            node.execute_tick()

    def test_idle_child_does_not_advance_a_sequence(self):
        seq = SequenceNode("seq", [
            FunctionAction("idle", lambda _: IDLE),
            FunctionAction("after", lambda _: S),
        ])
        with pytest.raises(TypeError):
            seq.execute_tick()

    def test_idle_child_does_not_advance_a_fallback(self):
        fb = FallbackNode("fb", [
            FunctionAction("idle", lambda _: IDLE),
            FunctionAction("after", lambda _: F),
        ])
        with pytest.raises(TypeError):
            fb.execute_tick()

    def test_none_is_still_rejected(self):
        node = FunctionAction("bad", lambda _: None)

        # FunctionAction coerces None → FAILURE, so bypass it with a raw node.
        class _NoReturn(ActionNode):
            def tick(self):
                return None

        with pytest.raises(TypeError, match="expected NodeStatus"):
            _NoReturn("none", NodeConfig()).execute_tick()
        assert node.execute_tick() is F

    @pytest.mark.parametrize("status", [R, S, F])
    def test_valid_results_pass(self, status):
        assert FunctionAction("ok", lambda _: status).execute_tick() is status


# ─────────────────────────────────────────────────────────────────────────────
# #8 — SubTree must not drop _on_halt()
# ─────────────────────────────────────────────────────────────────────────────

class TestSubTreeHaltLifecycle:
    def test_on_halt_runs_when_running(self):
        halted: list = []

        class MySubTree(SubTree):
            def _on_halt(self):
                halted.append("cleanup")

        child = FunctionAction("c", lambda _: R)
        st = MySubTree("st", child)
        assert st.execute_tick() is R
        st.halt()

        assert halted == ["cleanup"]
        assert st.status is IDLE
        assert child.status is IDLE

    def test_on_halt_not_run_when_idle(self):
        halted: list = []

        class MySubTree(SubTree):
            def _on_halt(self):
                halted.append("cleanup")

        st = MySubTree("st", FunctionAction("c", lambda _: S))
        st.halt()
        assert halted == []

    def test_subtree_does_not_override_halt(self):
        assert "halt" not in SubTree.__dict__, (
            "SubTree.halt() must stay inherited from DecoratorNode so _on_halt() runs"
        )


# ─────────────────────────────────────────────────────────────────────────────
# #10 — Inspector must learn about halts; unnamed trees must not share a bb
# ─────────────────────────────────────────────────────────────────────────────

class TestHaltNotifiesInspector:
    def test_halted_leaf_leaves_running_nodes(self):
        insp = Inspector()
        node = FunctionAction("run", lambda _: R)
        node._inspector = insp

        node.execute_tick()
        assert node.uid in insp.running_nodes()
        assert node.uid in insp.active_path()

        node.halt()
        assert node.uid not in insp.running_nodes()
        assert node.uid not in insp.active_path()

    def test_halted_control_node_leaves_running_nodes(self):
        insp = Inspector()
        child = FunctionAction("run", lambda _: R)
        seq = SequenceNode("seq", [child])
        for n in (seq, child):
            n._inspector = insp

        seq.execute_tick()
        assert {seq.uid, child.uid} <= set(insp.running_nodes())

        seq.halt()
        assert insp.running_nodes() == []
        assert insp.active_path() == []

    def test_halted_decorator_leaves_running_nodes(self):
        insp = Inspector()
        child = FunctionAction("run", lambda _: R)
        inv = Inverter("inv", child)
        for n in (inv, child):
            n._inspector = insp

        inv.execute_tick()
        assert {inv.uid, child.uid} <= set(insp.running_nodes())

        inv.halt()
        assert insp.running_nodes() == []

    def test_idle_node_halt_does_not_notify(self):
        insp = Inspector()
        calls: list = []
        insp.on_node_halt = lambda *a, **k: calls.append(a)  # type: ignore[assignment]
        node = FunctionAction("done", lambda _: S)
        node._inspector = insp
        node.execute_tick()
        node.halt()
        assert calls == []


class TestUnnamedTreesHaveSeparateBlackboards:
    def test_two_unnamed_trees_are_independent(self):
        t1 = _tree(FunctionAction("a", lambda _: S), id_="")
        t2 = _tree(FunctionAction("b", lambda _: S), id_="")

        assert t1.blackboard is not t2.blackboard
        t1.blackboard.set("leaked", 1)
        assert not t2.blackboard.has("leaked")

    def test_two_unnamed_builder_trees_are_independent(self):
        def _build():
            b = TreeBuilder()
            b.action("a", lambda: True)
            return b.build()

        t1, t2 = _build(), _build()
        assert t1.blackboard is not t2.blackboard
        t1.blackboard.set("leaked", 1)
        assert not t2.blackboard.has("leaked")

    def test_tree_blackboard_is_not_the_memoised_global(self):
        t = _tree(FunctionAction("a", lambda _: S), id_="")
        assert t.blackboard is not Blackboard.create("__tree__")

    def test_named_trees_are_independent_too(self):
        t1 = _tree(FunctionAction("a", lambda _: S), id_="same_name")
        t2 = _tree(FunctionAction("b", lambda _: S), id_="same_name")
        assert t1.blackboard is not t2.blackboard

    def test_explicit_blackboard_is_still_honoured(self):
        bb = Blackboard(scope_name="explicit")
        t = Tree(TreeMetadata(id="explicit"), FunctionAction("a", lambda _: S), bb)
        assert t.blackboard is bb


# ─────────────────────────────────────────────────────────────────────────────
# #16 — tip() reports RUNNING only (documentation fix; locks in the behaviour)
# ─────────────────────────────────────────────────────────────────────────────

class TestTipIsRunningOnly:
    def test_tip_is_none_after_success(self):
        seq = SequenceNode("seq", [FunctionAction("a", lambda _: S)])
        tree = _tree(seq, "tip_success")
        assert tree.tick_once() is S
        assert tree.tip() is None

    def test_tip_is_the_running_leaf(self):
        leaf = FunctionAction("a", lambda _: R)
        inv = Inverter("inv", leaf)
        tree = _tree(inv, "tip_running")
        assert tree.tick_once() is R
        assert tree.tip() is leaf

    @pytest.mark.parametrize("cls", [TreeNode, ControlNode, DecoratorNode, LeafNode])
    def test_tip_docstring_does_not_promise_success(self, cls):
        doc = cls.tip.__doc__ or ""
        assert "RUNNING or SUCCESS" not in doc
