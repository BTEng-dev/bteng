# Reactive Execution Internals

Standard `SequenceNode` and `FallbackNode` remember the child that returned `RUNNING`
and resume there on the next tick. This is efficient, but it means earlier conditions
are **not** re-evaluated while a later action is still running — a changed precondition
has no effect until the current action finishes.

Reactive variants fix this by restarting from child[0] on every tick where relevant
state has changed.

---

## Reactive node types

| Node | Behavior |
|------|----------|
| `ReactiveSequenceNode` | Restarts from child[0] each tick; earlier conditions can interrupt running actions |
| `ReactiveFallbackNode` | Same semantics with fallback (OR) logic; higher-priority branches can preempt work |

---

## The dirty-flag implementation

BTEng implements reactive re-evaluation via **blackboard subscriptions**, not naive
polling. This is important for performance: conditions are only re-evaluated when
relevant state has actually changed.

**Step by step:**

1. When an action child enters `RUNNING`, the reactive parent calls
   `_collect_blackboards()`, which walks the subtree and collects every `Blackboard`
   instance reachable from any node in that subtree.

2. The parent subscribes a `_mark_dirty` callback to each collected blackboard.

3. When any of those blackboards receives a write (`bb.set(...)`), `_mark_dirty` is
   called and `_dirty = True` is set on the reactive parent.

4. On the next tick:
   - If `_dirty is True` → full re-evaluation from child[0]. `_dirty` is reset.
   - If `_dirty is False` → fast path: **guards ahead of the running child are still
     re-ticked**; only the running action is skipped (not re-entered).

   A "guard" is a `CONDITION` child, or a wrapper whose leaves are all conditions —
   so `<Inverter><IsStuck/></Inverter>`, the ordinary way to write a negated guard,
   counts. Anything with an action underneath does not.

   Before 0.3.1 the fast path skipped guards too, and only a blackboard write could
   force a re-check. That silently broke the node's headline contract for any guard
   whose truth comes from outside the blackboard — a ROS topic, a sensor handle, a
   clock — and made the semantics depend on whether the children happened to carry a
   blackboard at all. The dirty flag now governs only whether the *action* is
   re-entered from the start.

5. When the running action leaves `RUNNING` (succeeds, fails, or is halted), the
   reactive parent unsubscribes all callbacks and clears `_bb_subscriptions`.

**Fallback for trees without a blackboard:**

If `_collect_blackboards()` finds no blackboards in the subtree (e.g., the tree was
built without one), `_bb_subscriptions` is empty. The tick guard
`not self._bb_subscriptions` causes `_dirty` to be treated as always `True` — full
re-evaluation every tick. Since 0.3.1 this is a performance difference only: guards
are re-checked either way, so adding or removing a blackboard no longer changes what
the node *does*.

---

## Performance characteristics

| Scenario | Behaviour |
|----------|-----------|
| No blackboard write between ticks | Fast path: guards re-ticked, running action not re-entered |
| One blackboard write between ticks | Full re-evaluation from child[0] |
| Multiple writes between ticks | Same as one write — `_dirty` is already `True` |
| Subtree has no blackboard | Full re-evaluation every tick (original polling) |

The reactive fast path is equivalent in cost to the standard non-reactive path. The
overhead only occurs when a write actually happens.

---

## Example — interrupt on guard failure

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("robot")
bb.set("path_clear", True)
bb.set("goal", (1.0, 2.0))

tree = (
    TreeBuilder(blackboard=bb)
    .reactive_sequence("navigate_safely")
        .condition("PathClear", lambda: bb.get("path_clear", False))
        .action("Navigate",     lambda: NodeStatus.RUNNING)   # simulates long navigation
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)

print(executor.tick_once())          # PathClear=True → RUNNING

bb.set("path_clear", False)          # write triggers dirty flag

print(executor.tick_once())          # PathClear=False → FAILURE, Navigate halted
```

Expected output:

```text
NodeStatus.RUNNING
NodeStatus.FAILURE
```

---

## Example — priority preemption with ReactiveFallback

`ReactiveFallbackNode` enables priority-based preemption: a higher-priority branch
interrupts lower-priority work when it becomes available.

```python
bb = Blackboard.create("robot")
bb.set("emergency", False)

tree = (
    TreeBuilder(blackboard=bb)
    .reactive_fallback("priority_arbiter")
        .sequence("emergency_stop")
            .condition("Emergency",  lambda: bb.get("emergency", False))
            .action("Brake",         lambda: NodeStatus.SUCCESS)
        .end()
        .action("NormalWork", lambda: NodeStatus.RUNNING)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)

print(executor.tick_once())           # Emergency=False → NormalWork RUNNING

bb.set("emergency", True)            # triggers dirty flag

print(executor.tick_once())           # Emergency=True → Brake SUCCESS, NormalWork halted
```

Expected output:

```text
NodeStatus.RUNNING
NodeStatus.SUCCESS
```

---

## When to use reactive vs standard nodes

| Scenario | Recommended node |
|----------|-----------------|
| A condition change must halt a running action | `ReactiveSequenceNode` |
| A higher-priority alternative should preempt current work | `ReactiveFallbackNode` |
| The action should run to completion without interruption | Standard `SequenceNode` |
| Conditions are checked only at the start of each task | Standard `SequenceNode` |
| No blackboard is used | Either — reactive falls back to polling |

> [!WARNING]
> **Keep guard conditions cheap**
>
> Guard conditions inside a reactive node are re-evaluated on every tick where a
> blackboard write occurred. They should read already-computed state from the
> blackboard — not perform blocking operations or expensive computation. Move any
> heavy computation into an action that writes its result to the blackboard, then
> check the result with a condition.

---

## Implementation location

| Component | File |
|-----------|------|
| `ReactiveSequenceNode` | `bteng/nodes/control/reactive_sequence.py` |
| `ReactiveFallbackNode` | `bteng/nodes/control/reactive_fallback.py` |
| `Blackboard.subscribe()` | `bteng/blackboard/blackboard.py` |
| `_collect_blackboards()` | Defined on `ControlNode` in `bteng/core/node.py` |
