# TreeBuilder Tutorial

`TreeBuilder` is the recommended way to build BTEng trees in Python. It provides a
fluent, indentation-based API that mirrors the visual structure of the tree without
requiring you to manually construct `NodeConfig` objects or manage parent-child
relationships.

---

## Basic structure

Every control node opens a scope. Close each scope with `.end()`. Leaf nodes (actions
and conditions) do not need `.end()`.

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("tutorial")
bb.set("battery_ok", True)
bb.set("path_clear", True)

tree = (
    TreeBuilder(blackboard=bb)
    .tree_id("Mission")
    .sequence("mission")
        .condition("BatteryOK", lambda: bb.get("battery_ok", False))
        .fallback("move_or_stop")
            .sequence("move")
                .condition("PathClear", lambda: bb.get("path_clear", False))
                .action("Navigate",     lambda: NodeStatus.SUCCESS)
            .end()
            .action("Stop", lambda: NodeStatus.SUCCESS)
        .end()
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

The Python indentation in your code visually represents the tree hierarchy. Each
`.end()` closes the nearest open scope.

---

## Builder rules

| Rule | Detail |
|------|--------|
| Close every control scope | `.sequence(...)`, `.fallback(...)`, `.parallel(...)` all need `.end()` |
| Close every decorator scope | `.retry(...)`, `.inverter()`, `.timeout(...)` need `.end()` |
| A decorator scope has exactly one child | Zero or more than one child → `RuntimeError` at `build()` |
| Call `.build()` last | Raises `RuntimeError` if unclosed scopes remain |
| `.tree_id()` is optional | Defaults to `"tree"` if omitted |

---

## Adding class-based nodes

`.node()` looks a node up **by registered type name** — it takes strings, not a class.
Register the class once with `@register_node`, then refer to it by that name:

```python
from bteng import ActionNode, NodeStatus, register_node

@register_node("Work")
class Work(ActionNode):
    def tick(self):
        return NodeStatus.SUCCESS

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .node("Work", "DoWork")     # type name first, then this instance's name
    .end()
    .build()
)
```

> [!NOTE]
> The signature is `.node(type_name, node_name="", attrs=None)`. All three are plain
> values — passing the class itself raises
> `KeyError: Unknown node type`. Omitting `node_name` auto-names the instance.

---

## Port mapping

Connect a node's declared ports to blackboard keys immediately after `.node()`:

```python
from bteng import ActionNode, InputPort, NodeStatus, OutputPort, register_node

@register_node("Navigate")
class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("goal"),
            OutputPort("result"),
        ]

    def tick(self):
        goal = self.get_input("goal")
        self.set_output("result", f"arrived:{goal}")
        return NodeStatus.SUCCESS

tree = (
    TreeBuilder(blackboard=bb)
    .node("Navigate", "Nav")
        .map("goal", "current_goal")        # input port "goal" reads bb["current_goal"]
        .map_output("result", "nav_result") # output port "result" writes bb["nav_result"]
    .build()
)
```

For static values that do not come from the blackboard:

```python
.node("Navigate", "Nav")
    .literal("goal", (0.0, 0.0))   # hardcoded value
```

The builder validates that `.map()` and `.map_output()` reference ports that are
actually declared in `provided_ports()`. If not, `TreeExecutor.set_tree()` raises
`TreeValidationError`.

---

## Decorators

Wrap a single child with a decorator scope:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .retry(max_attempts=3)
            .action("TryConnect", lambda: NodeStatus.FAILURE)
        .end()
        .inverter()
            .condition("NotBusy", lambda: False)
        .end()
        .timeout(msec=5000.0)
            .node("SlowAction", "SlowTask")
        .end()
    .end()
    .build()
)
```

Available decorator methods:

| Method | Parameters | Effect |
|--------|------------|--------|
| `.inverter()` | — | Flips `SUCCESS` ↔ `FAILURE` |
| `.retry(max_attempts)` | `max_attempts: int = 3` | Re-ticks on `FAILURE` up to N times |
| `.timeout(msec)` | `msec: float = 1000.0` | Returns `FAILURE` after the deadline. **Milliseconds, not seconds** |
| `.force_success()` | — | Always returns `SUCCESS` |
| `.force_failure()` | — | Always returns `FAILURE` |

`RateController` has no `TreeBuilder` shortcut. Reach it through the factory
(`.node("RateController", "throttle", {"hz": 10.0})`) or define the tree in XML.

---

## Reactive nodes

Use `.reactive_sequence()` and `.reactive_fallback()` in place of the standard
variants:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .reactive_sequence("guarded_navigate")
        .condition("PathClear", lambda: bb.get("path_clear", False))
        .action("Navigate",     NavigateAction)
    .end()
    .build()
)
```

Any blackboard write to a key watched by a condition in this subtree will trigger
re-evaluation from `PathClear` on the next tick, even if `Navigate` is still
`RUNNING`.

---

## Full example — multi-phase mission

```python
from bteng import (
    Blackboard, NodeStatus, StatefulActionNode, TreeBuilder, TreeExecutor, register_node,
)

bb = Blackboard.create("mission")
bb.set("battery_ok",    True)
bb.set("goal_reached",  False)
bb.set("path_clear",    True)
bb.set("recovery_ok",   True)

@register_node("NavigateToGoal")
class NavigateToGoal(StatefulActionNode):
    def on_start(self):
        self._ticks = 0
        return NodeStatus.RUNNING

    def on_running(self):
        self._ticks += 1
        if self._ticks >= 4:
            bb.set("goal_reached", True)
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING

tree = (
    TreeBuilder(blackboard=bb)
    .tree_id("FullMission")
    .fallback("mission_or_recover")
        .sequence("main_mission")
            .condition("BatteryOK",   lambda: bb.get("battery_ok",   False))
            .reactive_sequence("guarded_nav")
                .condition("PathClear", lambda: bb.get("path_clear", False))
                .node("NavigateToGoal", "Navigate")
            .end()
            .condition("GoalReached", lambda: bb.get("goal_reached", False))
        .end()
        .sequence("recovery")
            .condition("RecoveryOK",  lambda: bb.get("recovery_ok",  False))
            .action("ReturnHome",     lambda: NodeStatus.SUCCESS)
        .end()
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
status = executor.tick_until_result(max_ticks=20)
print(status)
print("Goal reached:", bb.get("goal_reached"))
```

Expected output:

```text
NodeStatus.SUCCESS
Goal reached: True
```

---

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `.end()` | Close every control and decorator scope; `build()` raises `RuntimeError` if left open |
| Calling `.build()` before all `.end()` | Same as above — finish all scopes first |
| Passing a class or instance to `.node()` | `.node()` takes **strings**: `.node("Navigate", "Nav")`. Register the class first with `@register_node("Navigate")` |
| Using `.map()` on a node with no `provided_ports()` | `TreeExecutor` will raise `TreeValidationError` for unknown ports |
| Mixing `TreeBuilder` and manual node construction in one tree | Possible but fragile; prefer one or the other |

Next: [Testing your first tree](testing-first-tree.md).
