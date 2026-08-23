# Testing Your First Tree

BTEng includes mock nodes and a small test harness that let you unit-test behavior
trees without real hardware, network calls, or side effects. The goal is to test tree
structure and control logic in isolation.

---

## BehaviorTreeTest

`BehaviorTreeTest` runs a tree with configurable expectations and returns a structured
result:

```python
from bteng import (
    BehaviorTreeTest,
    MockActionNode,
    MockConditionNode,
    NodeStatus,
    SequenceNode,
    Tree,
    TreeMetadata,
)

condition = MockConditionNode("Ready")
condition.set_bool(True)             # returns SUCCESS every tick

action = MockActionNode("Work")
action.set_ticks_to_complete(2)      # returns RUNNING×2, then SUCCESS

root = SequenceNode("root", children=[condition, action])
tree = Tree(TreeMetadata(id="first_test"), root)

result = (
    BehaviorTreeTest(tree)
    .expect_final_status(NodeStatus.SUCCESS)
    .set_max_ticks(10)
    .run()
)

print(result.passed)
```

Expected output:

```text
True
```

`TestResult` carries three fields: `passed`, `error_message`, and `violations`. For
per-node tick counts, read `node.tick_count` on the node itself.

If expectations are not met (wrong final status, exceeded max ticks), `result.passed`
is `False` and `result.error_message` contains a description.

```python
assert result, result.error_message   # short-circuit assertion
```

---

## Mock nodes

### MockActionNode

Configures an action to return a preset sequence of statuses:

```python
from bteng import MockActionNode, NodeStatus

mock = MockActionNode("Navigate")

# Option 1: run for N ticks, then return the forced result
mock.set_ticks_to_complete(3)           # RUNNING, RUNNING, then SUCCESS

# Option 2: choose the status it settles on (default SUCCESS)
mock.set_status(NodeStatus.FAILURE)

# Option 3: take over tick() entirely for an explicit sequence
statuses = iter([NodeStatus.RUNNING, NodeStatus.RUNNING, NodeStatus.SUCCESS])
mock.set_callback(lambda: next(statuses))
```

> [!NOTE]
> `set_ticks_to_complete(n)` returns `RUNNING` for **n-1** ticks and the forced result
> on tick n. `set_ticks_to_complete(3)` gives `RUNNING, RUNNING, SUCCESS` — not three
> `RUNNING` ticks.

Query state after the run:

```python
mock.tick_count           # how many times execute_tick() was called
mock.tick_count_local     # how many times this mock's own tick() ran
mock.status               # current NodeStatus
mock.reset_count()        # clear the counters between phases of a test
```

To check that a running action was interrupted, assert on its status: a node that was
`RUNNING` and then halted ends at `NodeStatus.IDLE`, and its `tick_count` stops
advancing. See [Testing reactive behavior](#testing-reactive-behavior) below.

### MockConditionNode

Returns a preset boolean result:

```python
from bteng import MockConditionNode

cond = MockConditionNode("IsReady")
cond.set_bool(True)     # returns SUCCESS
cond.set_bool(False)    # returns FAILURE
```

### SimulatedActionNode

Simulates a long-running action with configurable tick duration:

```python
from bteng import NodeStatus, SimConfig, SimulatedActionNode

sim = SimulatedActionNode("LongTask", SimConfig(delay_ticks=5))
# RUNNING while the delay elapses, then SUCCESS

# fail instead, once the delay has elapsed
sim = SimulatedActionNode("LongTask", SimConfig(delay_ticks=5, result=NodeStatus.FAILURE))
```

`SimConfig` takes `delay_ticks`, `result`, and `force_failure_injection`.

Useful when you want to test a Retry or Timeout decorator without writing a custom
mock.

---

## Testing with a real blackboard

When a node reads or writes the blackboard, construct the tree with a real blackboard
and verify the blackboard state after the run:

```python
from bteng import (
    Blackboard, BehaviorTreeTest, ConditionNode, MockActionNode,
    NodeConfig, NodeStatus, SequenceNode, Tree, TreeMetadata,
)

bb = Blackboard.create("test_bb")
bb.set("ready", True)

class IsReady(ConditionNode):
    def tick(self):
        return NodeStatus.SUCCESS if self.blackboard.get("ready") else NodeStatus.FAILURE

cfg  = NodeConfig(blackboard=bb)
mock = MockActionNode("Work")
mock.set_ticks_to_complete(1)

root = SequenceNode("root", children=[IsReady("check", cfg), mock])
tree = Tree(TreeMetadata(id="bb_test"), root)

result = (
    BehaviorTreeTest(tree)
    .expect_final_status(NodeStatus.SUCCESS)
    .run()
)
assert result, result.error_message

Blackboard.reset("test_bb")   # clean up between tests
```

---

## Testing reactive behavior

A reactive node re-checks its guard every tick and halts the running action when the
guard stops passing. To test that, flip the blackboard value between ticks and assert
on the action's status:

```python
from bteng import (
    Blackboard, ConditionNode, MockActionNode, NodeConfig, NodeStatus,
    ReactiveSequenceNode, Tree, TreeExecutor, TreeMetadata,
)

bb  = Blackboard.create("reactive_test")
bb.set("path_clear", True)
cfg = NodeConfig(blackboard=bb)

class PathClear(ConditionNode):
    def tick(self):
        return NodeStatus.SUCCESS if self.blackboard.get("path_clear") else NodeStatus.FAILURE

work = MockActionNode("Work", cfg)
work.set_ticks_to_complete(99)          # never finishes on its own

root = ReactiveSequenceNode("root", children=[PathClear("guard", cfg), work])
executor = TreeExecutor()
executor.set_tree(Tree(TreeMetadata(id="reactive_test"), root))

executor.tick_once()                    # guard passes -> work starts
assert work.status is NodeStatus.RUNNING
assert work.tick_count == 1

bb.set("path_clear", False)             # guard now fails
assert executor.tick_once() is NodeStatus.FAILURE

assert work.status is NodeStatus.IDLE   # halted, not finished
assert work.tick_count == 1             # never ticked again

Blackboard.reset("reactive_test")
```

`NodeStatus.IDLE` is the signal: a node that completes on its own ends at `SUCCESS` or
`FAILURE`, while a node that was interrupted is reset to `IDLE`.

---

## Integration with pytest

### Stop blackboards leaking between tests

`Blackboard.create(name)` returns a **process-wide singleton** — two calls with the same
name give you the same object. Whatever one test writes is still there for the next one,
which is how a test that passes alone starts failing inside the suite.

BTEng ships a pytest plugin that removes the chore. Enable it once in your `conftest.py`:

```python
pytest_plugins = ["bteng.testing.plugin"]
```

Every test now starts and ends with all named blackboards cleared. No `Blackboard.reset()`
calls to remember.

It also provides `bteng_blackboard`, a throwaway blackboard scoped to one test and shared
with nothing:

```python
def test_navigation(bteng_blackboard):
    bteng_blackboard.set("goal", (1.0, 2.0))
    ...
```

`Blackboard.reset_all()` does the same clearing manually if you are not using pytest.

### Standard patterns

Beyond that, no special support is needed:

```python
import pytest
from bteng import Blackboard

@pytest.fixture(autouse=True)
def clean_blackboard():
    """Reset shared blackboard state between every test."""
    yield
    Blackboard.reset("robot")

def test_navigate_succeeds():
    from bteng import (
        BehaviorTreeTest, MockActionNode, MockConditionNode,
        NodeStatus, SequenceNode, Tree, TreeMetadata,
    )

    cond = MockConditionNode("BatteryOK")
    cond.set_bool(True)

    action = MockActionNode("Navigate")
    action.set_ticks_to_complete(2)

    root = SequenceNode("root", children=[cond, action])
    tree = Tree(TreeMetadata(id="navigate_test"), root)

    result = (
        BehaviorTreeTest(tree)
        .expect_final_status(NodeStatus.SUCCESS)
        .set_max_ticks(10)
        .run()
    )
    assert result, result.error_message

def test_navigate_fails_when_battery_low():
    from bteng import (
        BehaviorTreeTest, MockActionNode, MockConditionNode,
        NodeStatus, SequenceNode, Tree, TreeMetadata,
    )

    cond = MockConditionNode("BatteryOK")
    cond.set_bool(False)       # battery is dead — sequence should fail immediately

    action = MockActionNode("Navigate")
    action.set_ticks_to_complete(5)

    root = SequenceNode("root", children=[cond, action])
    tree = Tree(TreeMetadata(id="battery_test"), root)

    result = (
        BehaviorTreeTest(tree)
        .expect_final_status(NodeStatus.FAILURE)
        .set_max_ticks(10)
        .run()
    )
    assert result, result.error_message
    assert action.tick_count == 0   # action was never reached
```

---

## Common testing mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `Blackboard.reset()` in teardown | `create(name)` is a process-wide singleton, so state leaks. Enable `pytest_plugins = ["bteng.testing.plugin"]` and it is handled for you |
| Testing implementation details instead of behavior | Assert on final status and blackboard state, not on internal node fields |
| Using `tick_until_result` in tests without `max_ticks` | A tree stuck in `RUNNING` will run forever; always set a limit |
| Assuming a reactive guard interrupted the action | The action may have finished naturally instead. Assert `node.status is NodeStatus.IDLE` and that `node.tick_count` stopped advancing |

Next: the [Practical Recipes](../recipes/guard-condition.md) section shows full
end-to-end patterns for common behavior-tree problems.
