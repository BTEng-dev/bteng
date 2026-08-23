# Factory & Plugins

`NodeFactory` is a singleton registry that maps string IDs to node classes.
The XML parser and `TreeBuilder` use it to resolve node types by name.

---

## Registering nodes

### Decorator style (recommended)

```python
from bteng import register_node, ActionNode, NodeStatus

@register_node()              # registers as "MyAction" (class name)
class MyAction(ActionNode):
    def tick(self): return NodeStatus.SUCCESS

@register_node("my_alias")   # registers under a custom name
class Another(ActionNode):
    def tick(self): return NodeStatus.SUCCESS
```

### Manual registration

```python
from bteng import NodeFactory

factory = NodeFactory.get_instance()
factory.register(MyAction)
factory.register(MyAction, name="alt_name")
```

---

## NodeManifest

Every registered node has a manifest derived from `provided_ports()`:

```python
manifest = factory.manifest("MyAction")
manifest.type_name         # "MyAction"
manifest.node_type         # NodeType.ACTION / CONDITION / CONTROL / DECORATOR
manifest.ports             # list of PortDefinition
manifest.description       # class docstring, if present
```

The manifest is used by the XML parser to seed port defaults and determine input/output
direction for `{key}` attributes.

> [!WARNING]
> **Use `provided_ports()`, not `define_ports()`**
>
> `define_ports()` is not read by the factory. Always declare ports via
> `provided_ports()`.

---

## Plugin system

Load additional node libraries at runtime without modifying the core package.

### From a file

```python
factory.load_plugin("/path/to/my_nodes.py")
```

The file is imported via `importlib`. If it defines `BTENG_NODES`, only those `(name,
class)` pairs are registered. Otherwise, all `TreeNode` subclasses defined in the
module are registered.

### From a module

```python
factory.load_module("my_package.bt_nodes")
```

Same discovery logic — uses `importlib.import_module`.

### Using the loader utility directly

```python
from bteng.plugins.loader import load_plugin_file, load_plugin_module

load_plugin_file("/path/to/nodes.py")
load_plugin_module("my_package.nodes")
```

---

## Exporting node models (XML)

Generate a `<TreeNodesModel>` XML block from all registered nodes — useful for IDE
tooling and external visualisers:

```python
xml_str = factory.export_node_models_xml()
print(xml_str)
```

---

## Example: plugin file

```python
# my_nodes.py

from bteng import ActionNode, ConditionNode, InputPort, NodeStatus

class MoveJ(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("speed", default="1.05")]

    def tick(self):
        speed = float(self.get_input("speed"))
        # ... move ...
        return NodeStatus.SUCCESS

class IsAtGoal(ConditionNode):
    def tick(self):
        return NodeStatus.SUCCESS   # real impl checks robot state

BTENG_NODES = [
    ("MoveJ", MoveJ),
    ("IsAtGoal", IsAtGoal),
]   # explicit list — only these are registered
```

Load it before parsing any XML that references these nodes:

```python
from bteng import BehaviorTreeEngine, Blackboard, NodeFactory

bb = Blackboard.create("robot")
NodeFactory.get_instance().load_plugin("my_nodes.py")
engine = BehaviorTreeEngine.from_xml("tree.xml", blackboard=bb)
```

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `NodeManifest` | dataclass | `bteng.factory.factory` | [factory.py](../../bteng/factory/factory.py) |
| `NodeFactory` | class | `bteng.factory.factory` | [factory.py](../../bteng/factory/factory.py) |
| `register_node` | function | `bteng.factory.factory` | [factory.py](../../bteng/factory/factory.py) |
| `load_plugin_file` | function | `bteng.plugins.loader` | [loader.py](../../bteng/plugins/loader.py) |
| `load_plugin_module` | function | `bteng.plugins.loader` | [loader.py](../../bteng/plugins/loader.py) |
