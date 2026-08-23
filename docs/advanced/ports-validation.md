# Ports and Validation

Ports are the typed data contract between a node and the blackboard. Declaring ports
lets BTEng validate the tree before the first tick, catching misconfigured nodes early
rather than at runtime.

For beginner port usage (declaring and using ports in a simple node), see
[Actions and Conditions](../beginner/actions-conditions.md#port-basics). This page
covers the full API and validation mechanics.

---

## Declaring ports

Override the class method `provided_ports()` and return a list of port declarations:

```python
from bteng import (
    ActionNode, BidirectionalPort, InputPort, NodeStatus, OutputPort, register_node,
)

@register_node("Navigate")
class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("goal",        description="Target position",  default="origin"),
            OutputPort("result",     description="Navigation outcome"),
            BidirectionalPort("counter"),   # this node reads and writes "counter"
        ]

    def tick(self):
        goal    = self.get_input("goal")             # reads bb[input_ports["goal"]]
        counter = self.get_input("counter", 0)       # reads bb["counter"]

        self.set_output("counter", counter + 1)      # writes bb["counter"]
        self.set_output("result",  f"arrived:{goal}")
        return NodeStatus.SUCCESS
```

The three port types:

| Type | Direction | Methods |
|------|-----------|---------|
| `InputPort` | Read from blackboard | `get_input(name, default=None)` |
| `OutputPort` | Write to blackboard | `set_output(name, value)` |
| `BidirectionalPort` | Read and write | Both `get_input` and `set_output` |

---

## Port default resolution

For any input port, BTEng resolves the value in this order:

1. **Explicit blackboard mapping** — set via `TreeBuilder.map("port", "bb_key")` or
   XML attribute `port="{bb_key}"` (note the curly braces).
2. **Static literal** — set via `TreeBuilder.literal("port", value)` or XML attribute
   `port="plain_string"` (no curly braces).
3. **`InputPort(default=...)`** — the class-level declared default.
4. **`get_input(name, default=...)`** — the call-site fallback.

If none of these are set, `get_input` returns `None`.

---

## Connecting ports in TreeBuilder

```python
tree = (
    TreeBuilder(blackboard=bb)
    .node("Navigate", "Nav")
        .map("goal",    "current_goal")      # input: reads bb["current_goal"]
        .map_output("result", "nav_result")  # output: writes bb["nav_result"]
        .literal("counter", 0)               # input: static value 0
    .build()
)
```

Mapping rules:
- `.map("port", "key")` — connects an input port to a blackboard key.
- `.map_output("port", "key")` — connects an output port to a blackboard key.
- `.literal("port", value)` — sets an input port to a static value (not a blackboard key).

---

## Connecting ports in XML

In XML, curly-brace syntax `{key}` means "blackboard lookup". Plain text means
"static literal":

```xml
<Action ID="Navigate"
        goal="{current_goal}"    <!-- reads bb["current_goal"] -->
        result="{nav_result}"    <!-- writes bb["nav_result"] -->
        counter="0"/>            <!-- static literal "0" -->
```

Use `provided_ports()` to declare ports on the Python class. The XML parser uses it
to validate attribute names at load time.

---

## Automatic validation

`TreeExecutor.set_tree()` runs `tree.validate()` before the first tick. It checks
every node that declares `provided_ports()`:

- Required input ports have a mapping, literal, or declared default.
- Output port targets are valid blackboard keys.
- No unknown port names are referenced.

On failure it raises `TreeValidationError`, which lists every issue found:

```python
from bteng import TreeValidationError

try:
    executor.set_tree(tree)
except TreeValidationError as e:
    for err in e.errors:
        print(f"{err.node_name}:{err.port_name} — {err.message}")
```

`TreeValidationError` is a subclass of `ValueError`. Catch it to display user-friendly
error messages in application startup code.

---

## Manual validation

Call `tree.validate()` directly to validate without a `TreeExecutor`:

```python
from bteng import Tree, TreeMetadata, TreeValidationError

tree = Tree(TreeMetadata(id="t"), root_node)
try:
    tree.validate()
except TreeValidationError as e:
    print(e)
```

This is useful when building or loading trees in a tool or editor that does not use
`TreeExecutor`.

---

## Nodes skipped by validation

Nodes with an empty `provided_ports()` — including all lambda-based nodes, mock nodes,
and control nodes — are silently skipped during validation. Validation only checks
nodes that explicitly declare a port contract.

---

## Error types

| Exception | Subclass of | When raised |
|-----------|-------------|-------------|
| `PortValidationError` | `ValueError` | A single port on a single node is misconfigured |
| `TreeValidationError` | `ValueError` | One or more `PortValidationError` found across the tree |

`TreeValidationError.errors` is a list of `PortValidationError` instances. Always
iterate over it to see all issues — a tree can have multiple misconfigured nodes.

---

## Full validation example

```python
from bteng import (
    ActionNode, InputPort, NodeStatus, OutputPort,
    Tree, TreeMetadata, TreeExecutor, TreeValidationError,
)

class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("goal"), OutputPort("result")]

    def tick(self):
        goal = self.get_input("goal")
        self.set_output("result", f"arrived:{goal}")
        return NodeStatus.SUCCESS

# Construct the node WITHOUT providing a mapping for "goal"
node = Navigate("nav")   # no NodeConfig with input_ports["goal"]
tree = Tree(TreeMetadata(id="t"), node)

executor = TreeExecutor()
try:
    executor.set_tree(tree)
except TreeValidationError as e:
    for err in e.errors:
        print(f"  {err.node_name}:{err.port_name} → {err.message}")
```

Expected output:

```text
  nav:goal → required input port has no mapping and no default
```

Add a mapping to fix it:

```python
from bteng import Blackboard, NodeConfig

bb = Blackboard.create("nav")
bb.set("current_goal", (1.0, 0.0))

cfg  = NodeConfig(blackboard=bb, input_ports={"goal": "current_goal"})
node = Navigate("nav", cfg)
tree = Tree(TreeMetadata(id="t"), node)
executor.set_tree(tree)   # no exception
```
