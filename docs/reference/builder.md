# TreeBuilder

`TreeBuilder` is a fluent Python DSL for constructing behavior trees without XML or
manual `NodeConfig` wiring.

---

## Basic usage

```python
from bteng import TreeBuilder, Blackboard, NodeStatus

bb = Blackboard.create("demo")

tree = (
    TreeBuilder(blackboard=bb)
    .tree_id("MyTree")
    .sequence("root")
        .condition("Check",    lambda: bb.get("ready", False))
        .action("Work",        lambda: NodeStatus.SUCCESS)
        .fallback("Recover")
            .retry(max_attempts=3)
                .action("Retry", lambda: NodeStatus.SUCCESS)
            .end()
            .action("GiveUp",  lambda: NodeStatus.FAILURE)
        .end()
    .end()
    .build()
)
```

---

## Stack rules

- `sequence()`, `fallback()`, `parallel()`, and all decorator methods open a scope.
- Every open scope **must** be closed with `end()`.
- Decorator scopes require exactly one child before `end()` — `RuntimeError` is raised
  if the child is missing.
- `build()` raises `RuntimeError` if any scopes remain unclosed.

---

## Control nodes

```python
.sequence("name")          # SequenceNode
.fallback("name")          # FallbackNode
.parallel("name",
    success_threshold=2,
    failure_threshold=1)   # ParallelNode
.reactive_sequence("name") # ReactiveSequenceNode
.reactive_fallback("name") # ReactiveFallbackNode
```

## Decorator nodes

```python
.inverter("name")
.retry(max_attempts=3)
.timeout(duration=5.0)
.rate_controller(hz=10.0)
.force_success()
.force_failure()
```

## Leaf nodes

```python
# Lambda — no class needed
.action("name",    lambda: NodeStatus.SUCCESS)
.condition("name", lambda: True)

# Registered class
.action("name",    MyActionClass)
.condition("name", MyConditionClass)
```

---

## Port mapping

After adding a leaf node that uses typed ports, chain `.map()` / `.map_output()` /
`.literal()` to configure its port bindings:

```python
.action("Move", MoveAction)
    .map("target", "current_goal")    # input port → blackboard key
    .map_output("arrived", "done")    # output port → blackboard key
    .literal("speed", 1.05)           # static parameter
```

---

## Full method reference

| Method | Description |
|--------|-------------|
| `tree_id(id)` | Set the tree's metadata ID |
| `sequence(name)` | Open a SequenceNode scope |
| `fallback(name)` | Open a FallbackNode scope |
| `parallel(name, ...)` | Open a ParallelNode scope |
| `reactive_sequence(name)` | Open a ReactiveSequenceNode scope |
| `reactive_fallback(name)` | Open a ReactiveFallbackNode scope |
| `inverter(name)` | Open an Inverter scope |
| `retry(max_attempts)` | Open a Retry scope |
| `timeout(duration)` | Open a Timeout scope |
| `rate_controller(hz)` | Open a RateController scope |
| `force_success()` | Open a ForceSuccess scope |
| `force_failure()` | Open a ForceFailure scope |
| `action(name, impl)` | Add an ActionNode leaf |
| `condition(name, impl)` | Add a ConditionNode leaf |
| `map(port, key)` | Map input port to blackboard key |
| `map_output(port, key)` | Map output port to blackboard key |
| `literal(port, value)` | Set a static port parameter |
| `end()` | Close the current scope |
| `build()` | Finalize and return `Tree` |

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `TreeBuilder` | class | `bteng.core.tree_builder` | [tree_builder.py](../../bteng/core/tree_builder.py) |
