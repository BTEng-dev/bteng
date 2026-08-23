# Guard Condition

A guard condition is a condition node that sits before a long-running action. When the
condition fails, the action is halted immediately — even if it has been running for
many ticks. This is the standard way to implement safety interlocks, priority
interrupts, and preemption in behavior trees.

---

## The problem

With a standard `SequenceNode`, the sequence remembers which child was `RUNNING` and
resumes there on the next tick. Earlier conditions are **not** re-checked while a
later action is still running:

```
Standard Sequence — tick 1:
├── PathClear  →  SUCCESS   (checked, passes)
└── Navigate   →  RUNNING   (starts navigation)

Standard Sequence — tick 2:
└── Navigate   →  RUNNING   (resumed here; PathClear is NOT re-checked)
```

If `path_clear` becomes `False` during tick 2, the robot keeps navigating — it won't
notice until `Navigate` finishes on its own.

---

## The solution: ReactiveSequenceNode

Use a `ReactiveSequenceNode`. It restarts from child[0] on every tick where a
blackboard write has occurred:

```
ReactiveSequence — tick 1:
├── PathClear  →  SUCCESS   (checked, passes)
└── Navigate   →  RUNNING   (starts navigation)

ReactiveSequence — tick 2 (after bb.set("path_clear", False)):
├── PathClear  →  FAILURE   (re-checked! condition fails)
└── Navigate   →  (halted)  (Navigate.halt() is called automatically)

ReactiveSequence returns FAILURE.
```

---

## Code

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("guard_recipe")
bb.set("path_clear", True)

tree = (
    TreeBuilder(blackboard=bb)
    .reactive_sequence("navigate_safely")
        .condition("PathClear", lambda: bb.get("path_clear", False))
        .action("Navigate",     lambda: NodeStatus.RUNNING)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)

# First tick — path is clear, Navigate starts
status = executor.tick_once()
print("Tick 1:", status)

# Something changes the world state
bb.set("path_clear", False)

# Second tick — PathClear is re-evaluated, Navigate is halted
status = executor.tick_once()
print("Tick 2:", status)
```

Expected output:

```text
Tick 1: NodeStatus.RUNNING
Tick 2: NodeStatus.FAILURE
```

---

## How BTEng implements this

When `Navigate` enters `RUNNING`, the `ReactiveSequenceNode` subscribes to all
`Blackboard` instances reachable in its subtree. Any subsequent write sets a dirty
flag. On the next tick, the dirty flag causes full re-evaluation from child[0].

Without a write between ticks the fast path applies, but it only skips *re-entering*
the running action — the guards ahead of it are re-ticked every tick regardless. So a
guard reading a ROS topic, a sensor handle or a clock interrupts the action just as
reliably as one reading the blackboard; the dirty flag only decides whether the branch
is re-evaluated from child[0].

---

## Variations

> [!NOTE]
> The snippets below assume an action class registered under the type name
> `NavigateAction`, e.g. `@register_node("NavigateAction")` on your own
> `ActionNode` subclass. `.node()` resolves nodes by that registered name.

### Multiple guards

A reactive sequence can have any number of conditions before the action. All of them
are re-checked when the blackboard is written:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .reactive_sequence("safe_navigate")
        .condition("BatteryOK",  lambda: bb.get("battery_level", 0) > 20)
        .condition("PathClear",  lambda: bb.get("path_clear", False))
        .condition("NoEmergency",lambda: not bb.get("emergency_stop", False))
        .node("NavigateAction", "Navigate")
    .end()
    .build()
)
```

If any condition fails, the sequence returns `FAILURE` and `Navigate` is halted.

### Nested guard with recovery

Wrap the guarded sequence in a fallback to handle interruption gracefully:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .fallback("navigate_or_wait")
        .reactive_sequence("guarded_navigate")
            .condition("PathClear", lambda: bb.get("path_clear", False))
            .node("NavigateAction", "Navigate")
        .end()
        .action("Wait", lambda: NodeStatus.RUNNING)  # wait until path clears
    .end()
    .build()
)
```

When `PathClear` fails, the reactive sequence fails, the fallback moves to `Wait`,
which returns `RUNNING`. On the next tick, if `PathClear` succeeds again, the reactive
sequence succeeds and the fallback short-circuits back to it.

---

## When to use guard conditions

| Situation | Use a reactive guard? |
|-----------|----------------------|
| Safety interlock that must stop motion immediately | Yes |
| Condition checked at start of task only | No — use standard `Sequence` |
| Priority preemption (higher-priority task becomes available) | Yes — use `ReactiveFallbackNode` |
| Condition that changes slowly or infrequently | Yes — reactive fast path is cheap when bb is not written |

> [!WARNING]
> **Keep guard conditions cheap**
>
> Guard conditions are re-evaluated on every tick where a blackboard write occurred.
> Avoid slow blocking calls (network, file I/O, heavy computation) inside a condition
> used as a guard. Conditions should read state, not compute it.
