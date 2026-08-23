# 5-minute Quick Start

This is the recommended first BTEng program. It uses `TreeBuilder` to construct the
tree and `TreeExecutor` to run it.

Create `quickstart.py`:

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("quickstart")
bb.set("battery_ok", True)

tree = (
    TreeBuilder(blackboard=bb)
    .tree_id("FirstTree")
    .sequence("mission")
        .condition("BatteryOK", lambda: bb.get("battery_ok", False))
        .action("Navigate", lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)

status = executor.tick_until_result(max_ticks=10)
print(status)
```

Run it:

```bash
python3 quickstart.py
```

Expected output:

```text
NodeStatus.SUCCESS
```

## What happened

The tree uses a `Sequence`, which means:

1. Tick `BatteryOK`.
2. If `BatteryOK` succeeds, tick `Navigate`.
3. Return `SUCCESS` only if both children succeed.

If the condition fails, the action is not run:

```python
bb.set("battery_ok", False)
```

Expected output:

```text
NodeStatus.FAILURE
```

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `.end()` | Close every `sequence`, `fallback`, `parallel`, and decorator scope with `.end()` |
| Starting with `BehaviorTreeEngine` | Use `TreeBuilder + TreeExecutor` for new code |
| Reusing blackboard names across tests | Call `Blackboard.reset("name")` in test teardown |
| Confusing literals and blackboard keys | Use `.literal("port", value)` for static values and `.map("port", "key")` for blackboard lookup |
| Returning arbitrary objects from actions | Return `NodeStatus.SUCCESS`, `NodeStatus.FAILURE`, `NodeStatus.RUNNING`, or a boolean for simple inline nodes |

## Where to go next

- [Which API should I use?](which-api.md) — decision table for the Python, XML and legacy APIs
- [Control nodes](../beginner/control-nodes.md) — Sequence, Fallback, Parallel
- [`examples/`](../../examples/) — two complete runnable programs:
  [`01_creature_comfort.py`](../../examples/01_creature_comfort.py) 🐹 to start, then
  [`02_industrial_cell.py`](../../examples/02_industrial_cell.py) 🏭
