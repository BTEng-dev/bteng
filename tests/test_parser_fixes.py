"""Regression tests for XMLTreeParser fixes.

Covers:
  - F1  Output/inout ports declared in Python (provided_ports) are honoured by
        the parser even without a hand-written <TreeNodesModel>.
  - F8  Parser instance state (tree index + document port model) no longer leaks
        between documents; a duplicate <Tree ID> is an error.
  - F7  parse_*_to_registry() no longer swallows per-tree build failures.
  - F12 Numeric XML attributes are validated with an error naming the element,
        the attribute and the offending value.
"""
from __future__ import annotations

import logging
from typing import List

import pytest

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import (
    BidirectionalPort, InputPort, NodeStatus, OutputPort, PortDefinition,
)
from bteng.factory.factory import NodeFactory
from bteng.nodes.leaf.action import ActionNode
from bteng.xml_parser.parser import XMLTreeParser


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

class Producer(ActionNode):
    """Declares an output port in Python only — no <TreeNodesModel> anywhere."""

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [InputPort("target"), OutputPort("result")]

    def tick(self) -> NodeStatus:
        self.wrote = self.set_output("result", 42)
        return NodeStatus.SUCCESS


class Accumulator(ActionNode):
    """Declares a bidirectional (INOUT) port."""

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        return [BidirectionalPort("counter")]

    def tick(self) -> NodeStatus:
        self.set_output("counter", (self.get_input("counter") or 0) + 1)
        return NodeStatus.SUCCESS


class Plain(ActionNode):
    """Declares no ports at all."""

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


@pytest.fixture
def factory():
    """A private factory so these tests never disturb the global singleton."""
    f = NodeFactory()
    f.register(Producer, "Producer")
    f.register(Accumulator, "Accumulator")
    f.register(Plain, "Plain")
    return f


def _parse(factory, xml, tree_id=None, blackboard=None):
    return XMLTreeParser(factory=factory).parse_string(
        xml, tree_id=tree_id, blackboard=blackboard
    )


def _first_child(root):
    return root.get_children()[0]


# ─────────────────────────────────────────────────────────────────────────────
# F1: output ports declared in Python were silently dropped
# ─────────────────────────────────────────────────────────────────────────────

PRODUCER_XML = (
    '<BTEng><Tree ID="main"><Sequence name="root">'
    '<Producer target="{goal}" result="{reading}"/>'
    '</Sequence></Tree></BTEng>'
)


class TestF1ManifestPortDirections:
    def test_declared_output_port_lands_in_output_ports(self, factory):
        node = _first_child(_parse(factory, PRODUCER_XML))
        assert node.config.output_ports == {"result": "reading"}
        assert node.config.input_ports == {"target": "goal"}
        assert "result" not in node.config.input_ports

    def test_output_port_value_reaches_the_blackboard(self, factory):
        """The whole point: set_output() used to return False and lose the value."""
        bb = Blackboard.create()
        node = _first_child(_parse(factory, PRODUCER_XML, blackboard=bb))

        assert node.execute_tick() == NodeStatus.SUCCESS
        assert node.wrote is True
        assert bb.get("reading") == 42

    def test_generic_node_tag_also_resolves_directions(self, factory):
        xml = (
            '<BTEng><Tree ID="main"><Sequence name="root">'
            '<Node type="Producer" result="{reading}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.output_ports == {"result": "reading"}

    def test_undeclared_attribute_still_defaults_to_input(self, factory):
        xml = (
            '<BTEng><Tree ID="main"><Sequence name="root">'
            '<Plain whatever="{key}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.input_ports == {"whatever": "key"}
        assert node.config.output_ports == {}

    def test_literal_attribute_is_still_a_param(self, factory):
        xml = (
            '<BTEng><Tree ID="main"><Sequence name="root">'
            '<Producer result="not_a_ref"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.params["result"] == "not_a_ref"
        assert node.config.output_ports == {}


class TestF1InoutPorts:
    def test_python_inout_port_lands_in_both_maps(self, factory):
        xml = (
            '<BTEng><Tree ID="main"><Sequence name="root">'
            '<Accumulator counter="{c}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.input_ports == {"counter": "c"}
        assert node.config.output_ports == {"counter": "c"}

    def test_inout_port_round_trips_through_the_blackboard(self, factory):
        xml = (
            '<BTEng><Tree ID="main"><Sequence name="root">'
            '<Accumulator counter="{c}"/>'
            '</Sequence></Tree></BTEng>'
        )
        bb = Blackboard.create()
        bb.set("c", 4)
        node = _first_child(_parse(factory, xml, blackboard=bb))
        node.execute_tick()
        assert bb.get("c") == 5

    def test_inout_port_tag_in_tree_nodes_model(self, factory):
        """<inout_port> is what export_node_models_xml() emits for INOUT."""
        xml = (
            '<BTEng>'
            '<TreeNodesModel>'
            '<Action ID="Plain"><inout_port name="slot"/></Action>'
            '</TreeNodesModel>'
            '<Tree ID="main"><Sequence name="root">'
            '<Plain slot="{s}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.input_ports == {"slot": "s"}
        assert node.config.output_ports == {"slot": "s"}

    def test_exported_model_round_trips(self, factory):
        """The factory's own export must parse back to the same directions."""
        model = factory.export_node_models_xml()
        xml = (
            '<BTEng>' + model +
            '<Tree ID="main"><Sequence name="root">'
            '<Accumulator counter="{c}"/><Producer result="{r}"/>'
            '</Sequence></Tree></BTEng>'
        )
        acc, prod = _parse(factory, xml).get_children()
        assert acc.config.input_ports == {"counter": "c"}
        assert acc.config.output_ports == {"counter": "c"}
        assert prod.config.output_ports == {"result": "r"}
        assert prod.config.input_ports == {}


class TestF1ExplicitModelWins:
    def test_tree_nodes_model_overrides_manifest_output(self, factory):
        """An explicit declaration beats provided_ports(), both directions."""
        xml = (
            '<BTEng>'
            '<TreeNodesModel>'
            '<Action ID="Producer"><input_port name="result"/></Action>'
            '</TreeNodesModel>'
            '<Tree ID="main"><Sequence name="root">'
            '<Producer result="{reading}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.input_ports == {"result": "reading"}
        assert node.config.output_ports == {}

    def test_tree_nodes_model_overrides_manifest_input(self, factory):
        xml = (
            '<BTEng>'
            '<TreeNodesModel>'
            '<Action ID="Producer"><output_port name="target"/></Action>'
            '</TreeNodesModel>'
            '<Tree ID="main"><Sequence name="root">'
            '<Producer target="{goal}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.output_ports == {"target": "goal"}
        assert node.config.input_ports == {}

    def test_attribute_absent_from_the_model_falls_back_to_manifest(self, factory):
        """A partial <TreeNodesModel> entry must not mask the other ports."""
        xml = (
            '<BTEng>'
            '<TreeNodesModel>'
            '<Action ID="Producer"><input_port name="target"/></Action>'
            '</TreeNodesModel>'
            '<Tree ID="main"><Sequence name="root">'
            '<Producer target="{goal}" result="{reading}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(_parse(factory, xml))
        assert node.config.input_ports == {"target": "goal"}
        assert node.config.output_ports == {"result": "reading"}

    def test_seeded_port_model_still_honoured(self, factory):
        """bteng_nav2 seeds parser._port_model before parsing; keep that working."""
        parser = XMLTreeParser(factory=factory)
        parser._port_model.update({"Plain": {"out": "output_port"}})
        xml = (
            '<BTEng><Tree ID="main"><Sequence name="root">'
            '<Plain out="{o}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(parser.parse_string(xml))
        assert node.config.output_ports == {"out": "o"}

    def test_document_model_beats_seeded_model(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser._port_model.update({"Plain": {"out": "output_port"}})
        xml = (
            '<BTEng>'
            '<TreeNodesModel>'
            '<Action ID="Plain"><input_port name="out"/></Action>'
            '</TreeNodesModel>'
            '<Tree ID="main"><Sequence name="root">'
            '<Plain out="{o}"/>'
            '</Sequence></Tree></BTEng>'
        )
        node = _first_child(parser.parse_string(xml))
        assert node.config.input_ports == {"out": "o"}
        assert node.config.output_ports == {}


# ─────────────────────────────────────────────────────────────────────────────
# F8: parser instance state leaked between documents
# ─────────────────────────────────────────────────────────────────────────────

DOC_A = (
    '<BTEng>'
    '<Tree ID="a"><Sequence name="a_root"><Plain/></Sequence></Tree>'
    '<Tree ID="helper"><Sequence name="helper_root"><Plain/></Sequence></Tree>'
    '</BTEng>'
)
DOC_B = (
    '<BTEng>'
    '<Tree ID="b"><Sequence name="b_root"><Plain/></Sequence></Tree>'
    '</BTEng>'
)


class TestF8DocumentIsolation:
    def test_second_document_gets_its_own_root(self, factory):
        parser = XMLTreeParser(factory=factory)
        assert parser.parse_string(DOC_A).name == "a_root"
        assert parser.parse_string(DOC_B).name == "b_root"

    def test_subtree_does_not_resolve_across_documents(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser.parse_string(DOC_A)

        leaky = (
            '<BTEng><Tree ID="b"><Sequence name="b_root">'
            '<SubTree ID="helper"/>'
            '</Sequence></Tree></BTEng>'
        )
        with pytest.raises(KeyError, match="helper"):
            parser.parse_string(leaky)

    def test_tree_id_from_first_document_is_gone(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser.parse_string(DOC_A)
        with pytest.raises(KeyError, match="'a'"):
            parser.parse_string(DOC_B, tree_id="a")

    def test_port_model_from_first_document_does_not_leak(self, factory):
        parser = XMLTreeParser(factory=factory)
        doc1 = (
            '<BTEng>'
            '<TreeNodesModel>'
            '<Action ID="Plain"><output_port name="out"/></Action>'
            '</TreeNodesModel>'
            '<Tree ID="t"><Sequence name="r"><Plain out="{o}"/></Sequence></Tree>'
            '</BTEng>'
        )
        first = _first_child(parser.parse_string(doc1))
        assert first.config.output_ports == {"out": "o"}

        doc2 = (
            '<BTEng>'
            '<Tree ID="t"><Sequence name="r"><Plain out="{o}"/></Sequence></Tree>'
            '</BTEng>'
        )
        second = _first_child(parser.parse_string(doc2))
        assert second.config.output_ports == {}
        assert second.config.input_ports == {"out": "o"}

    def test_registry_parse_also_resets(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser.parse_string(DOC_A)
        registry = parser.parse_string_to_registry(DOC_B)
        assert registry.ids() == ["b"]

    def test_reuse_after_a_failed_parse(self, factory):
        """A raised parse must not leave the instance poisoned."""
        parser = XMLTreeParser(factory=factory)
        with pytest.raises(ValueError):
            parser.parse_string(
                '<BTEng><Tree ID="x"><Nope/></Tree></BTEng>'
            )
        assert parser.parse_string(DOC_B).name == "b_root"


class TestF8DuplicateTreeId:
    DUP = (
        '<BTEng>'
        '<Tree ID="t"><Sequence name="first"><Plain/></Sequence></Tree>'
        '<Tree ID="t"><Sequence name="second"><Plain/></Sequence></Tree>'
        '</BTEng>'
    )

    def test_duplicate_id_raises_naming_the_id(self, factory):
        with pytest.raises(ValueError, match="t"):
            _parse(factory, self.DUP)

    def test_duplicate_id_message_says_duplicate(self, factory):
        with pytest.raises(ValueError) as exc:
            _parse(factory, self.DUP)
        assert "Duplicate" in str(exc.value)
        assert "'t'" in str(exc.value)

    def test_duplicate_id_raises_in_registry_parse_too(self, factory):
        with pytest.raises(ValueError, match="Duplicate"):
            XMLTreeParser(factory=factory).parse_string_to_registry(self.DUP)

    def test_distinct_ids_are_fine(self, factory):
        ok = (
            '<BTEng>'
            '<Tree ID="t1"><Sequence name="first"><Plain/></Sequence></Tree>'
            '<Tree ID="t2"><Sequence name="second"><Plain/></Sequence></Tree>'
            '</BTEng>'
        )
        assert _parse(factory, ok, tree_id="t2").name == "second"


# ─────────────────────────────────────────────────────────────────────────────
# F7: parse_*_to_registry() swallowed every per-tree error
# ─────────────────────────────────────────────────────────────────────────────

MIXED_XML = (
    '<BTEng>'
    '<Tree ID="good"><Sequence name="g"><Plain/></Sequence></Tree>'
    '<Tree ID="typo"><Sequence name="t"><Plani/></Sequence></Tree>'
    '<Tree ID="two_roots"><Plain/><Plain/></Tree>'
    '<Tree ID="also_good"><Sequence name="ag"><Plain/></Sequence></Tree>'
    '</BTEng>'
)


class TestF7RegistryErrorsSurfaced:
    def test_good_trees_are_still_registered(self, factory):
        registry = XMLTreeParser(factory=factory).parse_string_to_registry(MIXED_XML)
        assert set(registry.ids()) == {"good", "also_good"}

    def test_one_bad_tree_does_not_raise(self, factory):
        """Non-raising by contract: a bad tree must not kill the whole registry."""
        parser = XMLTreeParser(factory=factory)
        registry = parser.parse_string_to_registry(MIXED_XML)
        assert registry.get("good") is not None

    def test_failures_are_recorded_with_tree_id_and_exception(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser.parse_string_to_registry(MIXED_XML)

        failed = dict(parser.registry_errors)
        assert set(failed) == {"typo", "two_roots"}
        assert all(isinstance(e, Exception) for e in failed.values())
        assert "Plani" in str(failed["typo"])
        assert "one root child" in str(failed["two_roots"])

    def test_failures_are_logged_as_warnings(self, factory, caplog):
        parser = XMLTreeParser(factory=factory)
        with caplog.at_level(logging.WARNING, logger="bteng.xml_parser.parser"):
            parser.parse_string_to_registry(MIXED_XML)

        messages = [r.getMessage() for r in caplog.records]
        assert any("typo" in m for m in messages)
        assert any("two_roots" in m for m in messages)

    def test_registry_errors_empty_on_a_clean_document(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser.parse_string_to_registry(DOC_A)
        assert parser.registry_errors == []

    def test_registry_errors_are_reset_between_parses(self, factory):
        parser = XMLTreeParser(factory=factory)
        parser.parse_string_to_registry(MIXED_XML)
        assert parser.registry_errors
        parser.parse_string_to_registry(DOC_A)
        assert parser.registry_errors == []

    def test_tree_gets_its_own_child_scope_of_the_blackboard(self, factory):
        """What the docstring now claims: a child scope, not the shared board."""
        bb = Blackboard.create()
        bb.set("shared", "visible")
        registry = XMLTreeParser(factory=factory).parse_string_to_registry(
            DOC_A, blackboard=bb
        )
        tree_bb = registry.get("a").blackboard
        assert tree_bb is not bb
        assert tree_bb.get("shared") == "visible"       # parent keys fall through
        tree_bb.set("local", 1)
        assert bb.get("local") is None                  # writes stay local


# ─────────────────────────────────────────────────────────────────────────────
# F12: numeric XML attributes had no validation and no context
# ─────────────────────────────────────────────────────────────────────────────

def _tree(body: str) -> str:
    return f'<BTEng><Tree ID="main">{body}</Tree></BTEng>'


class TestF12NumericValidation:
    @pytest.mark.parametrize(
        "body, tag, attr",
        [
            ('<Parallel success_threshold="two"><Plain/><Plain/></Parallel>',
             "Parallel", "success_threshold"),
            ('<Parallel failure_threshold="1.5"><Plain/><Plain/></Parallel>',
             "Parallel", "failure_threshold"),
            ('<Retry max_attempts="2.5"><Plain/></Retry>', "Retry", "max_attempts"),
            ('<Retry num_attempts="x"><Plain/></Retry>', "Retry", "num_attempts"),
            ('<Timeout msec=""><Plain/></Timeout>', "Timeout", "msec"),
            ('<Timeout msec="abc"><Plain/></Timeout>', "Timeout", "msec"),
            ('<Timeout duration="soon"><Plain/></Timeout>', "Timeout", "duration"),
            ('<RateController hz="fast"><Plain/></RateController>',
             "RateController", "hz"),
        ],
    )
    def test_message_names_tag_attribute_and_value(self, factory, body, tag, attr):
        with pytest.raises(ValueError) as exc:
            _parse(factory, _tree(body))
        message = str(exc.value)
        assert f"<{tag} " in message, message
        assert attr in message, message

    def test_integer_attribute_says_integer(self, factory):
        with pytest.raises(ValueError, match="expected an integer"):
            _parse(factory, _tree('<Retry max_attempts="2.5"><Plain/></Retry>'))

    def test_float_attribute_says_number(self, factory):
        with pytest.raises(ValueError, match="expected a number"):
            _parse(factory, _tree('<Timeout msec=""><Plain/></Timeout>'))

    @pytest.mark.parametrize(
        "body, attr",
        [
            ('<Parallel success_threshold="{n}"><Plain/><Plain/></Parallel>',
             "success_threshold"),
            ('<Parallel failure_threshold="{n}"><Plain/><Plain/></Parallel>',
             "failure_threshold"),
            ('<Retry max_attempts="{n}"><Plain/></Retry>', "max_attempts"),
            ('<Retry num_attempts="{n}"><Plain/></Retry>', "num_attempts"),
            ('<Timeout duration="{d}"><Plain/></Timeout>', "duration"),
            ('<RateController hz="{rate}"><Plain/></RateController>', "hz"),
        ],
    )
    def test_a_blackboard_ref_binds_as_a_port(self, factory, body, attr):
        """These attributes are declared ports now, re-read every tick, so a
        {ref} resolves against the blackboard instead of being frozen at build
        time. Previously every one of them was rejected as "must be a literal"."""
        node = _parse(factory, _tree(body))
        assert node.config.input_ports.get(attr), node.config.input_ports
        assert attr not in node.config.params

    def test_msec_stays_a_build_time_literal_and_says_so(self, factory):
        """Timeout declares `duration`, not `msec` — binding msec would look
        right and do nothing, so it is rejected with the form that works."""
        with pytest.raises(ValueError) as exc:
            _parse(factory, _tree('<Timeout msec="{ms}"><Plain/></Timeout>'))
        message = str(exc.value)
        assert "literal" in message and "duration" in message, message

    def test_malformed_brace_form_is_also_rejected_as_non_literal(self, factory):
        with pytest.raises(ValueError, match="literal"):
            _parse(factory, _tree('<Retry max_attempts="{a b}"><Plain/></Retry>'))

    @pytest.mark.parametrize("hz", ["0", "0.0", "-1", "-0.5"])
    def test_non_positive_hz_is_rejected_at_parse_time(self, factory, hz):
        with pytest.raises(ValueError) as exc:
            _parse(factory, _tree(f'<RateController hz="{hz}"><Plain/></RateController>'))
        message = str(exc.value)
        assert "RateController" in message and "hz" in message, message
        assert "positive" in message, message

    def test_hz_zero_no_longer_builds_a_node_that_dies_later(self, factory):
        """It used to raise ZeroDivisionError from RateController.__init__."""
        with pytest.raises(ValueError):
            _parse(factory, _tree('<RateController hz="0"><Plain/></RateController>'))


class TestF12ValidValuesStillWork:
    def test_positive_hz(self, factory):
        root = _parse(factory, _tree('<RateController hz="10"><Plain/></RateController>'))
        assert root.execute_tick() == NodeStatus.SUCCESS

    def test_thresholds(self, factory):
        root = _parse(
            factory,
            _tree('<Parallel name="p" success_threshold="2" failure_threshold="1">'
                  '<Plain/><Plain/></Parallel>'),
        )
        assert root.execute_tick() == NodeStatus.SUCCESS

    def test_negative_success_threshold_is_the_all_children_sentinel(self, factory):
        """-1 is meaningful for Parallel, so only hz rejects non-positive values."""
        root = _parse(
            factory,
            _tree('<Parallel name="p" success_threshold="-1"><Plain/></Parallel>'),
        )
        assert root.execute_tick() == NodeStatus.SUCCESS

    def test_zero_msec_timeout(self, factory):
        root = _parse(factory, _tree('<Timeout msec="0"><Plain/></Timeout>'))
        assert root is not None

    def test_whitespace_around_a_number_is_tolerated(self, factory):
        root = _parse(factory, _tree('<Retry max_attempts=" 3 "><Plain/></Retry>'))
        assert root is not None

    def test_retry_and_timeout_defaults_when_attributes_absent(self, factory):
        assert _parse(factory, _tree('<Retry><Plain/></Retry>')) is not None
        assert _parse(factory, _tree('<Timeout><Plain/></Timeout>')) is not None
