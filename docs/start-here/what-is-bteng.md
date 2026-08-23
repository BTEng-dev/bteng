# What is BTEng?

BTEng is a behavior-tree runtime for Python. A behavior tree is a small decision
program made from nodes. On every **tick**, the tree traverses from the root downward
and asks each node for a result:

| Status | Meaning |
|--------|---------|
| `SUCCESS` | The node completed successfully |
| `FAILURE` | The node could not complete |
| `RUNNING` | The node is still working and should be ticked again |

The tree uses those results to decide what to do next — advance to the next step,
try a fallback, retry, or wait.

---

## When is BTEng useful?

BTEng is a good fit when your system needs any of these:

- **Preconditions** — only attempt an action when certain conditions are met
- **Fallback logic** — try option A, and if that fails, try option B
- **Retries** — retry a flaky operation up to N times before giving up
- **Recovery** — when the primary plan fails, execute a recovery sequence
- **Long-running tasks** — actions that take many ticks to complete (navigation,
  waiting for sensor data, uploading a file)
- **Priority interrupts** — stop what you are doing if a higher-priority condition
  changes

Common application domains:

- Robot missions and autonomy stacks
- Automation workflows
- Simulation agents and NPCs
- Game AI
- Tick-driven control systems

---

## How it compares to alternatives

| Alternative | Why behavior trees instead |
|-------------|---------------------------|
| State machine | Trees compose more naturally; no explicit transitions between every pair of states |
| Plain if/else logic | Trees separate decision structure from leaf implementation; easier to test and reuse |
| Coroutine / async loop | Trees provide built-in fallback, retry, and priority logic; execution is inspectable |
| Rule engine | Trees have deterministic, auditable tick-by-tick execution; easier to debug |

Behavior trees are not always the right tool. If your control logic is simple and
linear, an if/else or a coroutine is probably clearer. BTEng is most valuable when
you have nested priorities, multiple failure modes, or behavior that must be composed
from reusable pieces.

---

## Recommended first API

For new code, use `TreeBuilder` to construct a tree and `TreeExecutor` to run it:

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("robot")
bb.set("battery_ok", True)

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("mission")
        .condition("BatteryOK", lambda: bb.get("battery_ok", False))
        .action("Navigate",     lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_until_result(max_ticks=10))
```

That is the entire first API. XML loading, plugins, ZMQ streaming, runtime tree
modification, and the older `BehaviorTreeEngine` are all available when you need them,
but they are not required to get started.

---

## How BTEng fits together

| Concept | Role |
|---------|------|
| `NodeStatus` | The return value of every node tick: `SUCCESS`, `FAILURE`, `RUNNING` |
| Control node | Routes ticks to children: `Sequence` (AND), `Fallback` (OR), `Parallel` |
| Leaf node | Does actual work: `ActionNode` (tasks) or `ConditionNode` (checks) |
| Blackboard | Shared data store; nodes communicate through named keys |
| `TreeBuilder` | Fluent Python API for constructing trees |
| `TreeExecutor` | Runtime that ticks the tree and wires observability tools |

---

Next: [Core Concepts](concepts.md) for the full explanation of node types and execution
model, or go straight to [Install](install.md) if you prefer to learn by doing.
