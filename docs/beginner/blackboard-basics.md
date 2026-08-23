# Blackboard Basics

The blackboard is the shared data store for a behavior tree. Nodes communicate through
it rather than through direct references — an action writes a result, a condition reads
it, and neither node knows the other exists.

This decoupling is intentional. It means you can compose nodes freely without creating
dependencies between them.

---

## Creating and using a blackboard

```python
from bteng import Blackboard

bb = Blackboard.create("robot_state")   # named singleton

bb.set("goal",  (1.0, 2.0))
bb.set("speed", 0.5)
bb.set("flag",  None)          # None is a valid stored value

bb.get("goal")                 # (1.0, 2.0)
bb.get("speed")                # 0.5
bb.get("flag")                 # None  (the stored None, not the missing-key default)
bb.get("missing", "default")   # "default"  (key absent — fallback used)

bb.has("goal")                 # True
bb.has("flag")                 # True — None counts as set
bb.has("missing")              # False

bb.remove("goal")
bb.has("goal")                 # False
```

> [!NOTE]
> **Named singletons**
>
> `Blackboard.create("name")` always returns the same object for a given name.
> Two parts of your code can both call `Blackboard.create("robot_state")` and they
> get the same blackboard. This makes wiring up nodes easy, but it also means state
> leaks between tests unless you call `Blackboard.reset("name")` in teardown.

---

## Blackboard in a tree

The blackboard is passed to `TreeBuilder` at construction time and is available to all
nodes in the tree through `self.blackboard`:

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("demo")
bb.set("ready", True)
bb.set("goal",  "dock")

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .condition("Ready",     lambda: bb.get("ready", False))
        .action("PrintGoal",    lambda: print(bb.get("goal")) or NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_until_result(max_ticks=10))
```

Expected output:

```text
dock
NodeStatus.SUCCESS
```

---

## Reading and writing in class nodes

Inside a custom node class, access the blackboard via `self.blackboard`:

```python
from bteng import ActionNode, ConditionNode, NodeStatus

class ReadGoal(ActionNode):
    def tick(self):
        goal = self.blackboard.get("goal", None)
        if goal is None:
            return NodeStatus.FAILURE
        print(f"Moving to {goal}")
        return NodeStatus.SUCCESS

class SetResult(ActionNode):
    def tick(self):
        self.blackboard.set("mission_done", True)
        return NodeStatus.SUCCESS
```

For stricter data contracts with validation, use ports (`InputPort` / `OutputPort`)
instead of direct `get`/`set` calls. See [Actions and Conditions](actions-conditions.md)
for port basics.

---

## Snapshot and history

Take a point-in-time snapshot of all current keys and values:

```python
snapshot = bb.snapshot()   # dict copy — safe to inspect after the run
print(snapshot)
```

The blackboard records every write in a history log. Useful for debugging and
post-run analysis:

```python
history = bb.history("goal")     # list of BlackboardHistoryRecord
for record in history:
    print(record.timestamp, record.writer, record.value)
```

`BlackboardHistoryRecord` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `timestamp` | `float` | Unix timestamp of the write |
| `writer` | `str` | Name of the node that wrote (empty string if unset) |
| `value` | Any | The value that was written |
| `key` | `str` | The blackboard key |

---

## Change subscriptions

Subscribe a callback to be notified on any write:

```python
sub_id = bb.subscribe(lambda key, val: print(f"  {key} → {val}"))

bb.set("speed", 1.5)    # prints: speed → 1.5
bb.set("goal", "home")  # prints: goal → home

bb.unsubscribe(sub_id)
```

Subscriptions are used internally by `ReactiveSequenceNode` and `ReactiveFallbackNode`
to detect blackboard writes and trigger re-evaluation. When you use reactive nodes, the
subscription mechanism runs transparently in the background.

---

## Test isolation

Because `Blackboard.create()` returns a singleton, tests can leak state into each
other if you do not clean up. Always reset named blackboards in test teardown:

```python
import pytest
from bteng import Blackboard

@pytest.fixture(autouse=True)
def clean_blackboard():
    yield
    Blackboard.reset("robot_state")
```

Or reset manually between test runs:

```python
def teardown_function():
    Blackboard.reset("robot_state")
```

`Blackboard.reset("name")` clears all keys, history, and subscriptions for that
blackboard and removes it from the singleton registry.

---

## Child scopes (advanced)

For subtree isolation, a blackboard can have **child scopes** — isolated namespaces
with explicit key remapping to the parent. This is used by `SubTree` to prevent the
subtree's internal keys from leaking into the parent blackboard.

```python
parent = Blackboard.create("robot")
parent.set("goal", (1.0, 2.0))

# "local_goal" inside the child maps to "goal" in the parent
child = parent.create_child_scope("subtree", remapping={"local_goal": "goal"})

child.get("local_goal")             # reads parent["goal"] → (1.0, 2.0)
child.set("local_goal", (5.0, 0.0)) # writes parent["goal"]
print(parent.get("goal"))           # (5.0, 0.0)
```

Rules:
- Writes to a remapped key in a child scope are forwarded to the parent key.
- Reads for remapped keys resolve through the parent key.
- Keys not in the remapping fall through from child to parent.
- Non-remapped writes remain local to the child scope.

See [Blackboard scoping](../advanced/blackboard-scoping.md) for the full details and
subtree examples.

---

## Summary

| Operation | Method |
|-----------|--------|
| Store a value | `bb.set("key", value)` |
| Read a value | `bb.get("key", default)` |
| Check presence | `bb.has("key")` |
| Remove a key | `bb.remove("key")` |
| Snapshot all keys | `bb.snapshot()` |
| Read write history | `bb.history("key")` |
| Subscribe to writes | `bb.subscribe(callback)` |
| Clear for tests | `Blackboard.reset("name")` |

Next: [TreeBuilder tutorial](treebuilder-tutorial.md).
