"""Plugin loader helpers."""

from __future__ import annotations

from typing import List

from bteng.factory.factory import NodeFactory


def load_plugin_file(path: str, factory: NodeFactory | None = None) -> None:
    """Load a .py plugin file and register its nodes."""
    (factory or NodeFactory.get_instance()).load_plugin(path)


def load_plugin_module(module_name: str, factory: NodeFactory | None = None) -> None:
    """Load a Python module (importable name) and register its nodes."""
    (factory or NodeFactory.get_instance()).load_module(module_name)


def load_plugins(paths: List[str], factory: NodeFactory | None = None) -> None:
    """Batch load multiple plugin files."""
    f = factory or NodeFactory.get_instance()
    for p in paths:
        f.load_plugin(p)
