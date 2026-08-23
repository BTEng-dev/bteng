"""Tests for tree-construction-time port validation (#2)."""
from __future__ import annotations

import pytest

from bteng import (
    ActionNode, InputPort, OutputPort, BidirectionalPort,
    NodeConfig, NodeStatus, NodeFactory, register_node,
    Blackboard, SequenceNode,
    Tree, TreeMetadata, TreeExecutor, ExecutorConfig,
    PortValidationError, TreeValidationError,
)
from bteng.core.validation import validate_node, validate_tree


# ── Helpers ───────────────────────────────────────────────────────────────────

class FullyMappedAction(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("target"), OutputPort("result")]

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class RequiredInputAction(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("goal")]   # required — no default

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class DefaultedInputAction(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("speed", default=1.0)]   # optional — has default

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class NoPorts(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


def _make_tree(root):
    return Tree(TreeMetadata(id="test"), root)


# ── validate_node ─────────────────────────────────────────────────────────────

class TestValidateNode:
    def test_no_ports_skipped(self):
        node = NoPorts("n")
        assert validate_node(node) == []

    def test_fully_mapped_passes(self):
        bb  = Blackboard.create()
        cfg = NodeConfig(blackboard=bb, input_ports={"target": "goal_key"},
                         output_ports={"result": "result_key"})
        node = FullyMappedAction("a", cfg)
        assert validate_node(node) == []

    def test_static_param_satisfies_required_input(self):
        bb  = Blackboard.create()
        cfg = NodeConfig(blackboard=bb, params={"target": "fixed_value"})
        node = FullyMappedAction("a", cfg)
        errors = validate_node(node)
        # result is output only — no error; target covered by params
        assert all(e.port_name != "target" for e in errors)

    def test_missing_required_input_flagged(self):
        node = RequiredInputAction("a")
        errors = validate_node(node)
        assert len(errors) == 1
        assert errors[0].port_name == "goal"
        assert "required input" in errors[0].message

    def test_default_input_not_flagged(self):
        node = DefaultedInputAction("a")
        assert validate_node(node) == []

    def test_unknown_input_mapping_flagged(self):
        cfg = NodeConfig(input_ports={"nonexistent": "some_key"})
        node = NoPorts("a")
        # NoPorts has no ports so skipped — use RequiredInputAction instead
        cfg2 = NodeConfig(input_ports={"unknown_port": "key"})
        node2 = RequiredInputAction("a", cfg2)
        errors = validate_node(node2)
        port_names = {e.port_name for e in errors}
        assert "unknown_port" in port_names

    def test_unknown_output_mapping_flagged(self):
        cfg = NodeConfig(output_ports={"ghost": "key"})
        node = RequiredInputAction("a", cfg)
        errors = validate_node(node)
        port_names = {e.port_name for e in errors}
        assert "ghost" in port_names

    def test_error_fields_populated(self):
        node = RequiredInputAction("my_node")
        errors = validate_node(node)
        e = errors[0]
        assert e.node_name  == "my_node"
        assert e.node_uid   == node.uid
        assert e.node_type  == "RequiredInputAction"
        assert e.port_name  == "goal"
        assert isinstance(e.message, str)


# ── validate_tree ─────────────────────────────────────────────────────────────

class TestValidateTree:
    def test_clean_tree_returns_empty(self):
        bb  = Blackboard.create()
        cfg = NodeConfig(blackboard=bb, input_ports={"target": "k"},
                         output_ports={"result": "r"})
        root = FullyMappedAction("a", cfg)
        assert validate_tree(root) == []

    def test_errors_collected_across_multiple_nodes(self):
        root = SequenceNode("seq", children=[
            RequiredInputAction("a"),   # missing "goal"
            RequiredInputAction("b"),   # missing "goal"
        ])
        errors = validate_tree(root)
        assert len(errors) == 2
        assert {e.node_name for e in errors} == {"a", "b"}

    def test_deep_tree_traversed(self):
        bad = RequiredInputAction("leaf")
        mid = SequenceNode("mid", children=[bad])
        root = SequenceNode("root", children=[mid])
        errors = validate_tree(root)
        assert len(errors) == 1
        assert errors[0].node_name == "leaf"


# ── Tree.validate() ───────────────────────────────────────────────────────────

class TestTreeValidate:
    def test_valid_tree_no_exception(self):
        bb  = Blackboard.create()
        cfg = NodeConfig(blackboard=bb, input_ports={"target": "k"},
                         output_ports={"result": "r"})
        tree = _make_tree(FullyMappedAction("a", cfg))
        tree.validate()   # must not raise

    def test_invalid_tree_raises_tree_validation_error(self):
        tree = _make_tree(RequiredInputAction("a"))
        with pytest.raises(TreeValidationError) as exc_info:
            tree.validate()
        assert exc_info.value.errors
        assert "goal" in str(exc_info.value)

    def test_error_message_lists_all_problems(self):
        root = SequenceNode("seq", children=[
            RequiredInputAction("x"),
            RequiredInputAction("y"),
        ])
        tree = _make_tree(root)
        with pytest.raises(TreeValidationError) as exc_info:
            tree.validate()
        msg = str(exc_info.value)
        assert "'x'" in msg
        assert "'y'" in msg


# ── TreeExecutor.set_tree() ───────────────────────────────────────────────────

class TestExecutorValidation:
    def test_valid_tree_accepted(self):
        bb  = Blackboard.create()
        cfg = NodeConfig(blackboard=bb, input_ports={"target": "k"},
                         output_ports={"result": "r"})
        tree = _make_tree(FullyMappedAction("a", cfg))
        ex = TreeExecutor()
        ex.set_tree(tree)   # must not raise

    def test_invalid_tree_rejected_at_set_tree(self):
        tree = _make_tree(RequiredInputAction("a"))
        ex = TreeExecutor()
        with pytest.raises(TreeValidationError):
            ex.set_tree(tree)

    def test_error_contains_port_details(self):
        tree = _make_tree(RequiredInputAction("nav"))
        ex = TreeExecutor()
        with pytest.raises(TreeValidationError) as exc_info:
            ex.set_tree(tree)
        assert any(e.port_name == "goal" for e in exc_info.value.errors)

    def test_no_ports_node_always_accepted(self):
        tree = _make_tree(NoPorts("n"))
        ex = TreeExecutor()
        ex.set_tree(tree)


# ── TreeValidationError ───────────────────────────────────────────────────────

class TestTreeValidationError:
    def test_errors_attribute(self):
        errs = [PortValidationError("n", "uid", "T", "p", "msg")]
        exc = TreeValidationError(errs)
        assert exc.errors is errs

    def test_is_value_error(self):
        exc = TreeValidationError([])
        assert isinstance(exc, ValueError)

    def test_str_contains_node_and_port(self):
        errs = [PortValidationError("my_node", "uid", "MyType", "my_port", "something wrong")]
        msg = str(TreeValidationError(errs))
        assert "my_node" in msg
        assert "my_port" in msg
        assert "something wrong" in msg
