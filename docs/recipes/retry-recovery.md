# Retry with Recovery

Transient failures are common in real systems — network timeouts, sensor glitches,
physical interference. The retry-with-recovery pattern handles them without letting
a single failure abort the whole mission.

---

## The pattern

```
Fallback
├── Retry(N)
│   └── PrimaryAction          ← retried up to N times on FAILURE
└── RecoveryAction             ← runs only when all retries are exhausted
```

The `Retry` decorator re-ticks its child on `FAILURE`, up to `max_attempts` times. If
all attempts fail, `Retry` itself returns `FAILURE` and the `Fallback` moves to the
recovery branch.

The budget is also an input port, so a tree can take it from the blackboard rather
than freeze it at build time — `<Retry num_attempts="{max_tries}">`, with
`max_attempts` accepted as an alias. See [XML reference](../reference/xml.md).

---

## Minimal example

```python
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor, register_node

bb = Blackboard.create("retry_recipe")
attempts = {"count": 0}

def flaky_work():
    """Fails on the first call, succeeds on the second."""
    attempts["count"] += 1
    return NodeStatus.SUCCESS if attempts["count"] >= 2 else NodeStatus.FAILURE

tree = (
    TreeBuilder(blackboard=bb)
    .fallback("root")
        .retry(max_attempts=3)
            .action("TryWork", flaky_work)
        .end()
        .action("Recover", lambda: NodeStatus.SUCCESS)
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

`TryWork` fails on attempt 1 and succeeds on attempt 2. `Retry` sees `SUCCESS` and
returns `SUCCESS`, so the `Fallback`'s recovery branch is never reached.

---

## When the primary always fails

If all `max_attempts` are exhausted, `Retry` returns `FAILURE` and the `Fallback`
moves to the next branch:

```python
always_fail = lambda: NodeStatus.FAILURE

tree = (
    TreeBuilder(blackboard=bb)
    .fallback("root")
        .retry(max_attempts=3)
            .action("TryConnect", always_fail)
        .end()
        .action("UseCachedData", lambda: NodeStatus.SUCCESS)
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

`TryConnect` fails 3 times. `Retry` gives up and returns `FAILURE`. The `Fallback`
moves to `UseCachedData`, which succeeds.

---

## Multi-step recovery

Recovery can itself be a sequence of actions:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .fallback("navigate_or_recover")
        .retry(max_attempts=3)
            .node("NavigateAction", "Navigate")
        .end()
        .sequence("recovery_sequence")
            .action("BackUp",    lambda: NodeStatus.SUCCESS)
            .action("Replan",    lambda: NodeStatus.SUCCESS)
            .retry(max_attempts=2)
                .node("NavigateAction", "NavigateAgain")
            .end()
        .end()
        .action("AbortMission", lambda: NodeStatus.FAILURE)
    .end()
    .build()
)
```

The priority is:
1. Try `Navigate` up to 3 times.
2. If all 3 fail, try the recovery sequence: back up, replan, retry navigation up to 2 more times.
3. If recovery also fails, abort the mission.

---

## Retry with a timeout

Wrap both `Retry` and the action in a `Timeout` decorator to limit how long retries
can run in wall-clock time:

```python
tree = (
    TreeBuilder(blackboard=bb)
    .timeout(seconds=10.0)
        .retry(max_attempts=5)
            .node("UploadAction", "TryUpload")
        .end()
    .end()
    .build()
)
```

If all 5 attempts complete within 10 seconds and one succeeds, the tree returns
`SUCCESS`. If 10 seconds elapse before all attempts are exhausted, the `Timeout`
halts the subtree and returns `FAILURE`.

---

## Using Retry for waiting

`Retry` on a condition that checks for an external event is a simple "wait until ready"
pattern:

```python
@register_node("WaitForSensor")
class WaitForSensor(ConditionNode):
    def tick(self):
        return NodeStatus.SUCCESS if sensor_ready() else NodeStatus.FAILURE

tree = (
    TreeBuilder(blackboard=bb)
    .retry(max_attempts=100)
        .node("WaitForSensor", "WaitSensor")
    .end()
    .build()
)
```

> [!TIP]
> For polling with a tick rate, combine `Retry` with `RateController` to avoid
> hammering the sensor check at full tick speed.

---

## Reference

| Decorator | Parameters | Behavior |
|-----------|------------|----------|
| `Retry(n)` | `max_attempts: int` | Re-ticks child on `FAILURE` up to `n` times; passes `SUCCESS` and `RUNNING` through |
| `Timeout(t)` | `seconds: float` | Returns `FAILURE` if child does not finish within `t` seconds |
| `ForceFailure` | — | Converts child `SUCCESS` to `FAILURE`; useful for testing recovery paths |

`Retry` counts the total number of times the child returns `FAILURE`. It does not
count ticks where the child returned `RUNNING` — those do not consume an attempt.
