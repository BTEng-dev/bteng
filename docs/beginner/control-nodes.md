# Sequence, Fallback, Parallel

Control nodes decide which children are ticked and in what order. They never do work
themselves — they orchestrate the leaf nodes below them.

---

## Sequence (AND logic)

A `Sequence` ticks its children left-to-right. It succeeds only when **all** children
succeed. It stops at the first failure.

```
Sequence
├── A  →  SUCCESS   (move to next child)
├── B  →  RUNNING   (return RUNNING; resume B next tick, not A)
└── C               (not reached yet)
```

Think of a `Sequence` as a checklist: every item must pass, in order.

**Example — battery check before navigation:**

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("sequence_demo")
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
```

Expected output:

```text
NodeStatus.SUCCESS
```

Change `battery_ok` to `False` and the result becomes `NodeStatus.FAILURE` — the
sequence stops at `BatteryOK` and never ticks `PathClear` or `Navigate`.

**Memory behaviour:** standard `SequenceNode` remembers the child that returned
`RUNNING` and resumes from that child on the next tick. Earlier children are **not**
re-checked until the running child completes. If you need earlier conditions to
interrupt a running action, use `ReactiveSequenceNode` instead.

---

## Fallback / Selector (OR logic)

A `Fallback` ticks its children left-to-right. It succeeds when the **first** child
succeeds. It returns `FAILURE` only when all children fail.

```
Fallback
├── A  →  FAILURE   (try next)
├── B  →  SUCCESS   (return SUCCESS, C is skipped)
└── C               (not reached)
```

Think of a `Fallback` as a ranked list of alternatives: try the best option first,
then fall back to progressively simpler options.

**Example — navigate or stop:**

```python
bb = Blackboard.create("fallback_demo")
bb.set("path_clear", False)  # navigation will fail

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
```

Expected output:

```text
NodeStatus.SUCCESS
```

Because `path_clear` is `False`, the `PathClear` condition fails, the inner sequence
fails, so `Fallback` moves to `Stop`, which succeeds.

**Common pattern — recovery chain:**

```python
tree = (
    TreeBuilder(blackboard=bb)
    .fallback("main_or_recover")
        .sequence("main_plan")
            .condition("GoalReachable", ...)
            .action("Navigate",         ...)
        .end()
        .sequence("recovery")
            .action("BackUp",  ...)
            .action("Replan",  ...)
        .end()
        .action("GiveUp", lambda: NodeStatus.FAILURE)
    .end()
    .build()
)
```

---

## Parallel

A `Parallel` ticks **all** of its children on every tick, in the same thread,
sequentially. It returns based on configurable success and failure thresholds.

This is conceptual parallelism within the tree structure — all children are evaluated
each tick regardless of what any one child returns. It is not OS-level concurrency.
For background threads, use `AsyncActionNode`.

```python
from bteng import Blackboard, NodeStatus, ParallelNode, TreeBuilder, TreeExecutor

bb = Blackboard.create("parallel_demo")

tree = (
    TreeBuilder(blackboard=bb)
    .parallel("all_at_once", success_threshold=2, failure_threshold=1)
        .action("MonitorSensors", lambda: NodeStatus.SUCCESS)
        .action("LogData",        lambda: NodeStatus.SUCCESS)
        .action("Move",           lambda: NodeStatus.RUNNING)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
print(executor.tick_once())
```

Expected output:

```text
NodeStatus.RUNNING
```

Both `MonitorSensors` and `LogData` succeed (2 of 3), but `Move` is still `RUNNING`.
The threshold of `success_threshold=2` is met but `failure_threshold=1` is not, so the
whole parallel node returns `RUNNING` until either the failure threshold is hit or
`Move` finishes.

**Threshold reference:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `success_threshold` | `-1` (all children) | Number of children that must succeed. Any value `<= 0` means all; a value above the child count is clamped to it |
| `failure_threshold` | `1` | Number of children that must fail to return `FAILURE`. Clamped into `[1, child count]` |

Both thresholds are clamped to the live child count on purpose: a threshold that
can never be met used to leave the node `RUNNING` forever, because a child that
already finished is not re-ticked.

---

## Reactive variants

Standard `Sequence` and `Fallback` resume from the last-running child without
re-checking earlier conditions. Reactive variants restart from child[0] on every
tick where a blackboard write has occurred.

| Node | Use when |
|------|----------|
| `SequenceNode` | Earlier conditions do not need to preempt the running action |
| `ReactiveSequenceNode` | A condition change must interrupt the running action immediately |
| `FallbackNode` | Priority order is fixed; no need to re-check higher-priority branches |
| `ReactiveFallbackNode` | A higher-priority branch should preempt when it becomes available |

```python
# Navigate is immediately halted if PathClear becomes False
tree = (
    TreeBuilder(blackboard=bb)
    .reactive_sequence("safe_navigation")
        .condition("PathClear", lambda: bb.get("path_clear", False))
        .action("Navigate",     long_running_action)
    .end()
    .build()
)
```

---

## Decorator nodes

Decorators wrap a **single** child and modify its behavior or result. Always close the
decorator scope with `.end()`.

```python
tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .retry(max_attempts=3)
            .action("TryConnect", unreliable_action)
        .end()
        .inverter()
            .condition("NotBusy", lambda: bb.get("busy", False))
        .end()
        .timeout(seconds=5.0)
            .action("SlowTask", slow_action)
        .end()
    .end()
    .build()
)
```

| Decorator | Effect |
|-----------|--------|
| `Inverter` | Flips `SUCCESS` ↔ `FAILURE`; passes `RUNNING` through unchanged |
| `Retry(n)` | Re-ticks child on `FAILURE` up to `n` times; returns `FAILURE` after all attempts |
| `Timeout(t)` | Returns `FAILURE` if child takes longer than `t` seconds |
| `RateController(hz)` | Limits child ticking to `hz` ticks per second |
| `ForceSuccess` | Always returns `SUCCESS` (passes `RUNNING` through) |
| `ForceFailure` | Always returns `FAILURE` (passes `RUNNING` through) |

A decorator scope must contain **exactly one** child before `.end()`. `TreeBuilder`
raises `RuntimeError` at `build()` if a decorator scope has zero or more than one
child.

---

## Combining control nodes

Control nodes compose freely. Nested sequences and fallbacks let you express complex
prioritized behavior concisely:

```
Fallback "mission"
├── Sequence "primary_plan"
│   ├── Condition "BatteryOK"
│   ├── Condition "PathClear"
│   └── Action "Navigate"
└── Sequence "recovery"
    ├── Action "ReturnToBase"
    └── Action "Dock"
```

In this tree, the robot tries its primary plan first. If battery is low or path is
blocked, the primary sequence fails, and the fallback triggers the recovery sequence.

Next: [Actions and Conditions](actions-conditions.md).
