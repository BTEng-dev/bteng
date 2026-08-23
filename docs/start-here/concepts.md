# Core Concepts

This page explains the fundamental ideas behind behavior trees and how BTEng implements
them. Read this before the beginner guide if you are new to behavior trees, or use it
as a reference for specific topics.

---

## What is a behavior tree?

A behavior tree is a directed tree of nodes. On each **tick**, the tree is traversed
from the root downward. Each node reports a status to its parent, and control flows up
the tree based on those statuses.

Every node returns exactly one of three statuses:

| Status | Meaning |
|--------|---------|
| `SUCCESS` | The node completed successfully |
| `FAILURE` | The node failed or its condition was false |
| `RUNNING` | The node is still working; tick it again next frame |

`RUNNING` is what makes behavior trees different from decision trees. A node can span
many ticks — for example, a navigation action that takes several seconds to complete
returns `RUNNING` on each tick until it arrives.

---

## Leaf nodes

Leaf nodes do the actual work. There are two kinds:

**Action** — performs a task. May return `RUNNING` across many ticks.

```python
from bteng import ActionNode, NodeStatus

class Navigate(ActionNode):
    def tick(self):
        return NodeStatus.RUNNING
```

**Condition** — checks a predicate. Returns `SUCCESS` or `FAILURE` immediately; a
condition should never return `RUNNING`.

```python
from bteng import ConditionNode, NodeStatus

class BatteryOK(ConditionNode):
    def tick(self):
        level = self.blackboard.get("battery_level", 0)
        return NodeStatus.SUCCESS if level > 20 else NodeStatus.FAILURE
```

For one-off inline logic, BTEng also supports lambda-based leaves:

```python
.action("Navigate", lambda: NodeStatus.RUNNING)
.condition("BatteryOK", lambda: bb.get("battery_level", 0) > 20)
```

Lambdas may return `NodeStatus` or a boolean. `True` maps to `SUCCESS`, `False` maps
to `FAILURE`.

---

## Control nodes

Control nodes route ticks to their children based on results. They never do work
directly — they orchestrate.

### Sequence (AND logic)

Ticks children left-to-right. Succeeds only when all children succeed. Stops at the
first failure.

```
Sequence
├── A  →  SUCCESS   (move to next child)
├── B  →  RUNNING   (return RUNNING; resume here next tick)
└── C               (not reached yet)
```

A `Sequence` is the "do A, then B, then C" pattern. Think of it as a checklist —
if any step fails, the whole sequence fails.

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("sequence_example")
bb.set("battery_ok", True)
bb.set("path_clear", True)

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("mission")
        .condition("BatteryOK",  lambda: bb.get("battery_ok", False))
        .condition("PathClear",  lambda: bb.get("path_clear", False))
        .action("Navigate",      lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_until_result(max_ticks=10))
# NodeStatus.SUCCESS
```

### Fallback / Selector (OR logic)

Ticks children left-to-right. Succeeds when the first child succeeds. Returns
`FAILURE` only when all children fail.

```
Fallback
├── A  →  FAILURE   (try next)
├── B  →  SUCCESS   (return SUCCESS, skip C)
└── C               (not reached)
```

A `Fallback` is the "try this, or if that fails, try something else" pattern. Think
of it as a ranked list of alternatives.

```python
bb = Blackboard.create("fallback_example")
bb.set("path_clear", False)

tree = (
    TreeBuilder(blackboard=bb)
    .fallback("navigate_or_stop")
        .sequence("main_path")
            .condition("PathClear", lambda: bb.get("path_clear", False))
            .action("Navigate",     lambda: NodeStatus.SUCCESS)
        .end()
        .action("Stop", lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_until_result(max_ticks=10))
# NodeStatus.SUCCESS  (Stop succeeds because Navigate is blocked)
```

### Parallel

Ticks **all** children on every tick, in the same thread, sequentially. Returns based
on configurable success and failure thresholds.

```python
from bteng import ParallelNode

parallel = ParallelNode(
    "monitor_and_act",
    success_threshold=2,   # succeed when 2 children succeed
    failure_threshold=1,   # fail when 1 child fails
    children=[monitor, move, report],
)
```

Use `Parallel` to run conceptually simultaneous behaviors — monitoring sensors while
executing a motion, for example. Note that it is not true concurrency; for background
threads, use `AsyncActionNode`.

---

## Decorator nodes

A decorator wraps a single child and modifies its result or its execution rate.

| Decorator | Effect |
|-----------|--------|
| `Inverter` | Flips `SUCCESS` ↔ `FAILURE`; passes `RUNNING` through |
| `Retry(n)` | Re-ticks child on `FAILURE` up to `n` times; returns `FAILURE` after all attempts |
| `Timeout(t)` | Returns `FAILURE` if the child takes longer than `t` seconds |
| `RateController(hz)` | Limits how often the child is ticked |
| `ForceSuccess` | Always returns `SUCCESS`; passes `RUNNING` through |
| `ForceFailure` | Always returns `FAILURE`; passes `RUNNING` through |

```python
# Retry an unreliable action up to three times
tree = (
    TreeBuilder(blackboard=bb)
    .retry(max_attempts=3)
        .action("TryConnect", lambda: NodeStatus.FAILURE)
    .end()
    .build()
)
```

---

## Reactive nodes

Standard `SequenceNode` and `FallbackNode` remember which child was `RUNNING` and
resume there on the next tick. This is efficient, but it means earlier conditions are
**not** re-checked while a later action is still running.

**Reactive variants** fix this by restarting from child[0] every tick.

| Node | Behavior |
|------|----------|
| `ReactiveSequenceNode` | Re-evaluates earlier conditions so they can interrupt running actions |
| `ReactiveFallbackNode` | Re-evaluates higher-priority branches so priorities can preempt work |

BTEng implements reactive re-evaluation using **dirty-flag subscriptions** rather than
naive polling. When an action enters `RUNNING`, the reactive parent subscribes to all
`Blackboard` instances in its subtree. Any blackboard write sets a dirty flag,
triggering a full re-evaluation from child[0] on the next tick. Without a write, the
fast path still re-checks the guards ahead of the running child — it only avoids
re-entering the running action. Guards are therefore evaluated every tick whatever
the blackboard does, which is what makes a condition backed by a sensor or a topic
(rather than by the blackboard) able to interrupt the action.

```python
# PathClear is re-checked every tick while Navigate is running.
# If path_clear becomes False, Navigate is halted immediately.
tree = (
    TreeBuilder(blackboard=bb)
    .reactive_sequence("guarded_navigate")
        .condition("PathClear", lambda: bb.get("path_clear", False))
        .action("Navigate",     NavigateAction)
    .end()
    .build()
)
```

See [Reactive execution internals](../advanced/reactive-internals.md) for the full
implementation details.

---

## Blackboard

The blackboard is the shared data store for a tree. Nodes communicate through it
rather than through direct references — an action writes a result, a condition reads
it, and neither node knows the other exists.

```python
from bteng import Blackboard

bb = Blackboard.create("robot_state")

bb.set("goal", (1.0, 2.0))
bb.get("goal")                  # (1.0, 2.0)
bb.get("missing", "default")    # "default"
bb.has("goal")                  # True
```

`Blackboard.create("name")` is a named singleton. The same name always returns the
same blackboard instance. This makes it easy to share state across nodes that are
constructed in different parts of your code.

For subtree port isolation, child scopes allow scoped namespaces with explicit key
remapping. See [Blackboard scoping](../advanced/blackboard-scoping.md).

---

## Port system

Ports declare what data a node needs (inputs) and produces (outputs). They are the
typed interface between a node and the blackboard.

```python
from bteng import ActionNode, InputPort, OutputPort, NodeStatus

class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("goal",    description="Target position"),
            OutputPort("result", description="Navigation outcome"),
        ]

    def tick(self):
        goal = self.get_input("goal")
        # ... navigate to goal ...
        self.set_output("result", "arrived")
        return NodeStatus.SUCCESS
```

Port mappings connect a node's named ports to blackboard keys. They are set at
construction time via `TreeBuilder.map()` / `.literal()` or via XML attributes.
`TreeExecutor.set_tree()` validates all declared ports before the first tick.

See [Ports and validation](../advanced/ports-validation.md) for details.

---

## Tick loop

The tick loop is how the tree advances. One call to `executor.tick_once()` triggers
one complete traversal from the root.

```
executor.tick_once()
  └─ tree.tick_once()
       └─ root.execute_tick()
            └─ (children called recursively)
```

The tree is fully synchronous — one tick completes before the next begins. This makes
execution deterministic and easy to trace.

For background work (I/O, blocking calls, slow hardware), use `AsyncActionNode`. It
runs its work in a separate thread while the main tick loop continues returning
`RUNNING`.

### Running a tree

`TreeExecutor` provides several execution modes:

```python
from bteng import TreeExecutor

executor = TreeExecutor()
executor.set_tree(tree)

# Tick exactly once
status = executor.tick_once()

# Tick until SUCCESS or FAILURE (or max_ticks exceeded)
status = executor.tick_until_result(max_ticks=100)

# Background event loop with configurable tick rate
executor.start_event_loop()
# ... your application runs ...
executor.stop_event_loop()
```

---

## Summary

| Concept | Role |
|---------|------|
| `NodeStatus` | The return value of every tick: `SUCCESS`, `FAILURE`, or `RUNNING` |
| Leaf node | Does real work: `ActionNode` (tasks) or `ConditionNode` (checks) |
| Control node | Routes ticks: `Sequence` (AND), `Fallback` (OR), `Parallel` (all) |
| Decorator | Wraps one child and modifies its behavior |
| Blackboard | Shared data store; nodes communicate through named keys |
| Port | Typed declaration of what a node reads from and writes to the blackboard |
| `TreeBuilder` | Python API for constructing trees fluently |
| `TreeExecutor` | Runtime that ticks the tree and wires introspection tools |

Next: [Install](install.md), then [5-minute Quick Start](quickstart.md).
