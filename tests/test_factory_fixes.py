"""Regression tests for NodeFactory fixes (F5, F6, F15).

Covers:
  - F5  load_plugin() registers the module in sys.modules under its own
        __name__, *before* executing it (PEP 563 annotations / dataclasses),
        keeps two plugins apart, and reports import failures with the path.
  - F6  export_node_models_xml() escapes every interpolated attribute value.
  - F15 reset_instance() replays @register_node registrations, and register()
        warns when a name is rebound to a *different* class.
"""
from __future__ import annotations

import logging
import sys
import textwrap

import pytest

from bteng.core.node import (
    InputPort, NodeStatus, NodeType, OutputPort, PortDefinition, PortDirection,
)
from bteng.factory.factory import (
    NodeFactory, NodeManifest, PluginLoadError, register_node,
)
from bteng.nodes.leaf.action import ActionNode
from bteng.xml_parser.parser import XMLTreeParser


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PLUGIN_HEADER = (
    "from bteng.core.node import NodeStatus\n"
    "from bteng.nodes.leaf.action import ActionNode\n"
)


def _write_plugin(path, body: str, header: str = _PLUGIN_HEADER):
    path.write_text(header + textwrap.dedent(body))
    return path


def _plugin_module_keys():
    return {k for k in sys.modules if k.startswith("_bteng_plugin")}


@pytest.fixture()
def factory():
    """A fresh global singleton, restored afterwards."""
    NodeFactory.reset_instance()
    before = _plugin_module_keys()
    yield NodeFactory.get_instance()
    for key in _plugin_module_keys() - before:
        sys.modules.pop(key, None)
    NodeFactory.reset_instance()


# A module-level decorator: it runs exactly once per interpreter, which is the
# whole point of the F15 replay list.
@register_node()
class FactoryFixDecoratedAction(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# F5: load_plugin() and sys.modules
# ─────────────────────────────────────────────────────────────────────────────

class TestF5PluginModuleRegistration:
    def test_future_annotations_dataclass_plugin_loads(self, factory, tmp_path):
        """A plugin using PEP 563 annotations + @dataclass must import cleanly.

        dataclasses resolves string annotations through
        sys.modules[cls.__module__]; if that is None the plugin dies with
        AttributeError: 'NoneType' object has no attribute '__dict__'.
        """
        plugin = _write_plugin(
            tmp_path / "annotated.py",
            """
            from __future__ import annotations

            from dataclasses import dataclass, field
            from typing import List

            from bteng.core.node import NodeStatus
            from bteng.nodes.leaf.action import ActionNode

            @dataclass
            class Cfg:
                items: List[int] = field(default_factory=list)

            class AnnotatedAction(ActionNode):
                def tick(self): return NodeStatus.SUCCESS
            """,
            header="",
        )

        factory.load_plugin(str(plugin))

        assert factory.is_registered("AnnotatedAction")

    def test_module_registered_under_its_own_name(self, factory, tmp_path):
        plugin = _write_plugin(tmp_path / "simple.py", """
            class SimpleAction(ActionNode):
                def tick(self): return NodeStatus.SUCCESS
        """)

        factory.load_plugin(str(plugin))
        cls = factory._registry["SimpleAction"]

        mod = sys.modules.get(cls.__module__)
        assert mod is not None, (
            f"sys.modules[{cls.__module__!r}] is missing — string annotations "
            "in the plugin cannot be resolved."
        )
        assert mod.__name__ == cls.__module__
        assert getattr(mod, "SimpleAction", None) is cls

    def test_module_visible_during_execution(self, factory, tmp_path):
        """The module is in sys.modules while its body runs, not only after."""
        plugin = _write_plugin(tmp_path / "selfaware.py", """
            import sys

            SELF_VISIBLE = sys.modules.get(__name__) is not None

            class SelfAwareAction(ActionNode):
                def tick(self): return NodeStatus.SUCCESS
        """)

        factory.load_plugin(str(plugin))
        mod = sys.modules[factory._registry["SelfAwareAction"].__module__]

        assert mod.SELF_VISIBLE is True

    def test_two_plugins_with_same_class_name_stay_distinct(self, factory, tmp_path):
        """Same class name in two files → two distinct, separately reachable classes."""
        body = """
            class Shared(ActionNode):
                TAG = "{tag}"
                def tick(self): return NodeStatus.SUCCESS
        """
        p_a = _write_plugin(tmp_path / "plug_a.py", body.format(tag="A"))
        p_b = _write_plugin(tmp_path / "plug_b.py", body.format(tag="B"))

        factory.load_plugin(str(p_a))
        cls_a = factory._registry["Shared"]
        factory.load_plugin(str(p_b))
        cls_b = factory._registry["Shared"]

        assert cls_a is not cls_b
        assert cls_a.__module__ != cls_b.__module__, (
            "Both plugins reported the same __module__ — the second module "
            "object shadows the first."
        )
        # Both modules survive; each class is reachable through its own module.
        assert sys.modules[cls_a.__module__].Shared is cls_a
        assert sys.modules[cls_b.__module__].Shared is cls_b
        assert (cls_a.TAG, cls_b.TAG) == ("A", "B")

    def test_same_name_collision_is_not_silent(self, factory, tmp_path, caplog):
        """The registry can only hold one "Shared" — the shadowing must be logged."""
        body = """
            class Shared(ActionNode):
                def tick(self): return NodeStatus.SUCCESS
        """
        p_a = _write_plugin(tmp_path / "plug_a.py", body)
        p_b = _write_plugin(tmp_path / "plug_b.py", body)

        factory.load_plugin(str(p_a))
        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            factory.load_plugin(str(p_b))

        assert any("Shared" in r.getMessage() for r in caplog.records)

    def test_failing_plugin_names_the_file(self, factory, tmp_path):
        plugin = tmp_path / "broken.py"
        plugin.write_text("raise RuntimeError('boom inside plugin')\n")

        with pytest.raises(PluginLoadError) as exc_info:
            factory.load_plugin(str(plugin))

        message = str(exc_info.value)
        assert str(plugin) in message, f"Error does not name the plugin: {message}"
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "boom inside plugin" in str(exc_info.value.__cause__)
        # Still an ImportError, so existing handlers keep working.
        assert isinstance(exc_info.value, ImportError)

    def test_failing_plugin_leaves_no_module_behind(self, factory, tmp_path):
        plugin = tmp_path / "broken2.py"
        plugin.write_text("raise RuntimeError('boom')\n")
        before = _plugin_module_keys()

        with pytest.raises(PluginLoadError):
            factory.load_plugin(str(plugin))

        assert _plugin_module_keys() == before

    def test_missing_plugin_file_raises_import_error(self, factory, tmp_path):
        with pytest.raises(ImportError):
            factory.load_plugin(str(tmp_path / "does_not_exist.py"))


# ─────────────────────────────────────────────────────────────────────────────
# F6: export_node_models_xml() escaping
# ─────────────────────────────────────────────────────────────────────────────

class _CleanAction(ActionNode):
    """A tidy action."""

    @staticmethod
    def provided_ports():
        return [
            InputPort("goal", "the goal pose"),
            OutputPort("result", "where the result lands"),
        ]

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class _NastyAction(ActionNode):
    @staticmethod
    def provided_ports():
        return [InputPort("goal", 'set to "auto" if a < b & c')]

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class TestF6ExportNodeModelsXml:
    def test_clean_output_is_unchanged(self, factory):
        factory.register(_CleanAction, "CleanAction")

        xml = factory.export_node_models_xml()

        assert '  <Action ID="CleanAction">' in xml
        assert '    <input_port name="goal" description="the goal pose"/>' in xml
        assert (
            '    <output_port name="result" description="where the result lands"/>'
            in xml
        )

    def test_builtins_only_export_is_well_formed(self, factory):
        """Even a pristine factory used to emit invalid XML.

        ParallelNode documents ``success_threshold`` as
        "... (<=0 = all children)" — the bare ``<`` broke every consumer.
        """
        import xml.etree.ElementTree as ET

        ET.fromstring(factory.export_node_models_xml())

    def test_special_characters_survive_round_trip(self, factory):
        factory.register(_NastyAction, "NastyAction")

        xml = factory.export_node_models_xml()
        document = (
            "<BTEng>"
            f"{xml}"
            '<Tree ID="t"><Sequence name="s"/></Tree>'
            "</BTEng>"
        )

        # Must not raise ParseError — this is exactly what a Groot2/BT.CPP
        # consumer (and bteng's own parser) does with the exported model.
        root = XMLTreeParser(factory).parse_string(document)

        assert root is not None

    def test_raw_special_characters_are_escaped(self, factory):
        factory.register(_NastyAction, "NastyAction")

        line = next(
            ln for ln in factory.export_node_models_xml().splitlines()
            if "goal" in ln
        )

        assert "&amp;" in line and "&lt;" in line
        assert 'description="set to "auto"' not in line

    def test_special_characters_in_type_name_and_port_name(self, factory):
        import xml.etree.ElementTree as ET

        manifest = NodeManifest(
            type_name='Weird"Node',
            node_type=NodeType.ACTION,
            ports=[PortDefinition('a<b', PortDirection.INPUT, "x & y")],
            description="d",
        )
        factory.register(_CleanAction, 'Weird"Node', manifest=manifest)

        root = ET.fromstring(factory.export_node_models_xml())

        action = next(
            (el for el in root.findall("Action") if el.get("ID") == 'Weird"Node'),
            None,
        )
        assert action is not None, "type_name with a quote did not survive export"
        port = action.find("input_port")
        assert port is not None
        assert port.get("name") == "a<b"
        assert port.get("description") == "x & y"

    def test_round_trip_preserves_attribute_values(self, factory):
        import xml.etree.ElementTree as ET

        factory.register(_NastyAction, "NastyAction")
        root = ET.fromstring(factory.export_node_models_xml())

        port = root.find('.//Action[@ID="NastyAction"]/input_port')
        assert port is not None
        assert port.get("name") == "goal"
        assert port.get("description") == 'set to "auto" if a < b & c'


# ─────────────────────────────────────────────────────────────────────────────
# F15: reset_instance() and overwrite warnings
# ─────────────────────────────────────────────────────────────────────────────

class TestF15DecoratorRegistrationsSurviveReset:
    def teardown_method(self):
        NodeFactory.reset_instance()

    def test_decorated_node_registered_initially(self):
        assert NodeFactory.get_instance().is_registered("FactoryFixDecoratedAction")

    def test_decorated_node_survives_reset_instance(self):
        NodeFactory.reset_instance()

        factory = NodeFactory.get_instance()

        assert factory.is_registered("FactoryFixDecoratedAction"), (
            "reset_instance() permanently dropped a @register_node node — the "
            "module-level decorator never runs again."
        )
        assert factory._registry["FactoryFixDecoratedAction"] is FactoryFixDecoratedAction
        # Built-ins are still there too.
        assert factory.is_registered("Sequence")

    def test_decorated_node_survives_repeated_resets(self):
        for _ in range(3):
            NodeFactory.reset_instance()
            assert NodeFactory.get_instance().is_registered("FactoryFixDecoratedAction")

    def test_decorated_node_is_creatable_after_reset(self):
        from bteng.core.node import NodeConfig

        NodeFactory.reset_instance()
        node = NodeFactory.get_instance().create_leaf(
            "FactoryFixDecoratedAction", "n", NodeConfig()
        )

        assert node.tick() == NodeStatus.SUCCESS

    def test_replay_does_not_warn(self, caplog):
        NodeFactory.reset_instance()

        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            NodeFactory.get_instance()

        assert caplog.records == [], (
            f"Replaying decorator registrations logged: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_alias_decorator_survives_reset(self):
        @register_node("FactoryFixAlias")
        class _Aliased(ActionNode):
            def tick(self) -> NodeStatus:
                return NodeStatus.SUCCESS

        NodeFactory.reset_instance()

        assert NodeFactory.get_instance().is_registered("FactoryFixAlias")


class TestF15OverwriteWarning:
    def setup_method(self):
        NodeFactory.reset_instance()

    def teardown_method(self):
        NodeFactory.reset_instance()

    def test_overwriting_with_different_class_warns(self, caplog):
        class Impostor(ActionNode):
            def tick(self) -> NodeStatus:
                return NodeStatus.FAILURE

        factory = NodeFactory.get_instance()
        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            factory.register(Impostor, "Sequence")

        messages = [r.getMessage() for r in caplog.records]
        assert len(messages) == 1, messages
        assert "Sequence" in messages[0]
        assert "Impostor" in messages[0]
        # The overwrite itself still happens — warn, do not block.
        assert factory._registry["Sequence"] is Impostor

    def test_re_registering_same_class_is_silent(self, caplog):
        factory = NodeFactory.get_instance()
        sequence_cls = factory._registry["Sequence"]

        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            for _ in range(5):
                factory.register(sequence_cls, "Sequence")
                factory.register(_CleanAction, "CleanAction")
                factory.register(_CleanAction, "CleanAction")

        assert caplog.records == [], (
            "Idempotent re-registration must stay silent — register_nodes() "
            f"helpers call it repeatedly. Got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_register_many_idempotent_is_silent(self, caplog):
        factory = NodeFactory.get_instance()
        mapping = {"CleanAction": _CleanAction, "NastyAction": _NastyAction}

        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            factory.register_many(mapping)
            factory.register_many(mapping)

        assert caplog.records == []

    def test_first_registration_of_a_new_name_is_silent(self, caplog):
        factory = NodeFactory.get_instance()

        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            factory.register(_CleanAction, "BrandNewName")

        assert caplog.records == []

    def test_same_class_under_two_names_is_silent(self, caplog):
        factory = NodeFactory.get_instance()

        with caplog.at_level(logging.WARNING, logger="bteng.factory.factory"):
            factory.register(_CleanAction, "AliasOne")
            factory.register(_CleanAction, "AliasTwo")

        assert caplog.records == []
        assert factory.is_registered("AliasOne") and factory.is_registered("AliasTwo")
