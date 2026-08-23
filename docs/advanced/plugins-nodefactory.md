# Plugins and NodeFactory

`NodeFactory` is the global registry that maps string IDs to node classes. The XML
parser and `TreeBuilder.node(name_string)` use this registry to look up node types by
name at runtime.

---

## Registering a node

Use the `@register_node()` decorator to register a node class automatically when the
module is imported:

```python
from bteng import ActionNode, ConditionNode, NodeStatus, register_node

@register_node()
class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("goal"), OutputPort("result")]

    def tick(self):
        goal = self.get_input("goal")
        self.set_output("result", f"arrived:{goal}")
        return NodeStatus.SUCCESS

@register_node()
class BatteryOK(ConditionNode):
    def tick(self):
        level = self.blackboard.get("battery_level", 0)
        return NodeStatus.SUCCESS if level > 20 else NodeStatus.FAILURE
```

The decorator uses the class name as the registry ID by default. Pass a custom name
to override:

```python
@register_node(name="NavV2")
class Navigate(ActionNode):
    ...
```

---

## Using the factory directly

Access the global `NodeFactory` instance to look up or enumerate registered nodes:

```python
from bteng import NodeFactory

factory = NodeFactory.get_instance()

# Check registration
print(factory.is_registered("Navigate"))    # True

# Look up a manifest
manifest = factory.manifest("Navigate")
print(manifest.type_name)                   # "Navigate"
print(manifest.node_class)                  # <class 'Navigate'>
print(manifest.ports)                       # list of port declarations

# List all registered IDs
for name in factory.registered_names():
    print(name)
```

`NodeManifest` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `type_name` | `str` | The registered string ID |
| `node_class` | `type` | The Python class |
| `ports` | `list` | Port declarations from `provided_ports()` |

---

## Plugin files

A plugin is a Python module that registers nodes. You load it at runtime, before
parsing any XML that references those nodes.

### Automatic discovery

If the module defines classes decorated with `@register_node()`, importing the module
registers them automatically:

```python
from bteng.plugins.loader import load_plugin_file

load_plugin_file("my_robot_nodes.py")
# All @register_node() classes in that file are now in the factory
```

### Explicit registration list

For control over which classes are exported (useful in large modules), define
`BTENG_NODES` as a list of `(name, class)` pairs:

```python
# my_robot_nodes.py

from bteng import ActionNode, NodeStatus

class Navigate(ActionNode):
    def tick(self):
        return NodeStatus.SUCCESS

class Dock(ActionNode):
    def tick(self):
        return NodeStatus.SUCCESS

BTENG_NODES = [
    ("Navigate", Navigate),
    ("Dock",     Dock),
]
```

When `load_plugin_file` finds `BTENG_NODES`, it registers only those pairs and ignores
any other classes in the file. When `BTENG_NODES` is absent, it falls back to
auto-discovery of `@register_node()` classes.

---

## Loading a plugin module by name

If the plugin is on `sys.path` (installed as a package), use `load_plugin_module`:

```python
from bteng.plugins.loader import load_plugin_module

load_plugin_module("my_company.bteng_nodes")
```

---

## Typical workflow

1. Define your node classes in a module, decorated with `@register_node()`.
2. At application startup, call `load_plugin_file()` or `load_plugin_module()`.
3. Parse your XML tree — the XML parser will find the nodes by ID.
4. Call `executor.set_tree(tree)` — validation checks port declarations.

```python
from bteng import BehaviorTreeEngine, Blackboard
from bteng.plugins.loader import load_plugin_file

# Step 2: load nodes
load_plugin_file("robot_nodes.py")

# Step 3: parse XML (nodes must be registered before this)
bb = Blackboard.create("mission")
bb.set("battery_level", 80)
engine = BehaviorTreeEngine.from_xml("mission.xml", blackboard=bb)

# Step 4: run
print(engine.run_until_complete())
```

---

## When to use plugins vs inline nodes

| Situation | Approach |
|-----------|----------|
| Application with only Python trees | Inline classes with `TreeBuilder.node(name, Class)` — no factory needed |
| XML-driven behavior edited outside code | Register with `@register_node()` and load via `load_plugin_file()` |
| Distributing a reusable node library | Package with `@register_node()` decorators; users call `load_plugin_module()` |
| Multiple applications sharing nodes | Package the nodes and register per application |
