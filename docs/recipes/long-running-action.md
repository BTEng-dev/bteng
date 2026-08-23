# Long-running Action

Use `StatefulActionNode` when work spans multiple ticks and needs a lifecycle.
It provides three methods: `on_start()` (called once on activation), `on_running()`
(called every subsequent tick), and `on_halted()` (called if interrupted).

## Example

```python
from bteng import NodeStatus, StatefulActionNode, Tree, TreeExecutor, TreeMetadata

class CountToThree(StatefulActionNode):
    def on_start(self):
        self.count = 0
        return NodeStatus.RUNNING

    def on_running(self):
        self.count += 1
        return NodeStatus.SUCCESS if self.count >= 3 else NodeStatus.RUNNING

    def on_halted(self):
        pass   # release resources here if needed

tree = Tree(TreeMetadata(id="long_running_recipe"), CountToThree("counter"))

executor = TreeExecutor()
executor.set_tree(tree)

print(executor.tick_once())
print(executor.tick_once())
print(executor.tick_once())
print(executor.tick_once())
```

Expected output:

```text
NodeStatus.RUNNING
NodeStatus.RUNNING
NodeStatus.RUNNING
NodeStatus.SUCCESS
```

## How it works

`on_start()` runs on the first tick of each activation. If the node is halted by a
parent and re-entered later, `on_start()` runs again — state is reset automatically.
You do not need to track a `_started` flag.

`on_running()` runs on every tick after `on_start()` until the node returns `SUCCESS`
or `FAILURE`.

`on_halted()` is called when a parent node (e.g. a `ReactiveSequenceNode`) interrupts
the running action. Use it to release resources, cancel requests, or signal external
systems.

## When to use StatefulActionNode

| Situation | Recommendation |
|-----------|---------------|
| Action completes in one tick | Use plain `ActionNode.tick()` |
| Action spans multiple ticks, no blocking I/O | Use `StatefulActionNode` |
| Action involves blocking I/O or slow hardware | Use `AsyncActionNode` |

For the full API and practical examples including port declarations, see
[Stateful Actions](../beginner/stateful-action.md).
