# Actions and Conditions

Leaf nodes do the actual work. There are two kinds: **actions** that perform tasks,
and **conditions** that check whether something is true.

| Leaf type | Returns | Purpose |
|-----------|---------|---------|
| Condition | `SUCCESS` or `FAILURE` | Guards; checks a predicate |
| Action | `SUCCESS`, `FAILURE`, or `RUNNING` | Performs work; may span many ticks |

A condition should never return `RUNNING`. If it does, control nodes like `Sequence`
will wait indefinitely for it to complete.

---

## Inline leaves

For simple glue logic, pass a lambda to `.action()` or `.condition()`:

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("leaves")
bb.set("enabled", True)

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .condition("Enabled", lambda: bb.get("enabled", False))
        .action("Work",       lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_until_result(max_ticks=10))
```

Expected output:

```text
NodeStatus.SUCCESS
```

Inline leaves may return `NodeStatus` or a boolean:

| Return value | Coerced to |
|--------------|------------|
| `True` | `NodeStatus.SUCCESS` |
| `False` | `NodeStatus.FAILURE` |
| `NodeStatus.SUCCESS` | `NodeStatus.SUCCESS` |
| `NodeStatus.RUNNING` | `NodeStatus.RUNNING` |

Lambdas are good for quick experiments and one-off integration glue. For reusable
behavior, testing, or port-based data flow, use custom class nodes.

---

## Custom action node

Subclass `ActionNode` and override `tick()`:

```python
from bteng import ActionNode, NodeStatus

class Work(ActionNode):
    def tick(self):
        # do something
        return NodeStatus.SUCCESS
```

Plug it into a builder:

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("custom_action")

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .action("DoWork", Work)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_until_result(max_ticks=5))
```

---

## Custom condition node

Subclass `ConditionNode` and override `tick()`. Conditions have access to the
blackboard through `self.blackboard`:

```python
from bteng import ConditionNode, NodeStatus

class BatteryOK(ConditionNode):
    def tick(self):
        level = self.blackboard.get("battery_level", 0)
        return NodeStatus.SUCCESS if level > 20 else NodeStatus.FAILURE
```

The `blackboard` attribute is injected automatically by `TreeExecutor` (via the
`NodeConfig` passed at construction, or wired by `TreeBuilder`).

---

## Port basics

When a node needs to exchange data with the blackboard in a declared, validated way,
use ports. Ports are the typed interface between a node and the blackboard.

### Declaring ports

Override the class method `provided_ports()` and return a list of port declarations:

```python
from bteng import ActionNode, InputPort, OutputPort, NodeStatus, register_node

@register_node("Navigate")
class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("goal",        description="Target position"),
            OutputPort("result",     description="Navigation outcome"),
        ]

    def tick(self):
        goal = self.get_input("goal")           # reads from blackboard
        # ... do navigation work ...
        self.set_output("result", "arrived")    # writes to blackboard
        return NodeStatus.SUCCESS
```

### Connecting ports to blackboard keys

In `TreeBuilder`, use `.map()` and `.map_output()` immediately after declaring the node:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .node("Navigate", "Nav")
        .map("goal", "current_goal")       # input port "goal" reads bb["current_goal"]
        .map_output("result", "nav_done")  # output port "result" writes bb["nav_done"]
    .build()
)
```

For static values that do not come from the blackboard, use `.literal()`:

```python
.node("Navigate", "Nav")
    .literal("goal", (0.0, 0.0))   # hardcoded value, not a blackboard lookup
```

### Port default resolution

When BTEng resolves an input port value, it checks in this order:

1. Explicit blackboard mapping (`.map("port", "bb_key")`)
2. Static literal (`.literal("port", value)`)
3. `InputPort(default=...)` — declared in `provided_ports()`
4. `get_input("port", default=...)` — call-site fallback

### Validation

`TreeExecutor.set_tree()` validates all declared ports automatically. If a required
port is missing a mapping, it raises `TreeValidationError` before the first tick —
catching misconfigured trees early.

```python
from bteng import TreeValidationError

try:
    executor.set_tree(tree)
except TreeValidationError as e:
    for err in e.errors:
        print(err.node_name, err.port_name, err.message)
```

Nodes with no `provided_ports()` (lambdas, mock nodes, control nodes) are skipped
during validation.

For the full port and validation API, see [Ports and validation](../advanced/ports-validation.md).

---

## When to use which leaf type

| Situation | Use |
|-----------|-----|
| Quick logic with no reuse or state | Inline lambda |
| Reusable action with no cross-tick state | `ActionNode` subclass |
| Action that spans multiple ticks | `StatefulActionNode` (see next page) |
| Blocking I/O that must not block the tick loop | `AsyncActionNode` |
| Checking a blackboard value or system state | `ConditionNode` subclass |

Next: [Stateful Actions](stateful-action.md).
