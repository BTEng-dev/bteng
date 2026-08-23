# Async Action

Use `AsyncActionNode` when work must run in a background thread so that the main tick
loop is not blocked. Common cases: blocking network calls, file I/O, hardware
communication, or any operation that takes longer than one tick interval.

## How it works

`AsyncActionNode` launches `execute_async()` in a thread pool when first ticked.
While the background thread is running, `tick_once()` returns `RUNNING` immediately —
the tick loop continues at full speed. When `execute_async()` returns, the node picks
up the result on the next tick.

`TreeExecutor` injects a shared `ThreadPool` into every `AsyncActionNode` when
`set_tree()` is called. No manual wiring is needed.

## Example

```python
import time

from bteng import AsyncActionNode, CancellationToken, NodeStatus, Tree, TreeExecutor, TreeMetadata

class SlowWork(AsyncActionNode):
    def execute_async(self, token: CancellationToken):
        for _ in range(5):
            if token.is_cancelled():
                return NodeStatus.FAILURE
            time.sleep(0.01)
        return NodeStatus.SUCCESS

tree = Tree(TreeMetadata(id="async_recipe"), SlowWork("slow"))

executor = TreeExecutor()
executor.set_tree(tree)

print(executor.tick_once())   # returns RUNNING immediately; background thread starts
time.sleep(0.1)               # wait for background thread to finish
print(executor.tick_once())   # picks up SUCCESS from the completed thread
```

Expected output:

```text
NodeStatus.RUNNING
NodeStatus.SUCCESS
```

## Cancellation

The `CancellationToken` passed to `execute_async()` is set when the node is halted
by its parent. Poll `token.is_cancelled()` periodically inside `execute_async()` and
return early to avoid continuing work after the tree has moved on.

```python
def execute_async(self, token: CancellationToken):
    while not token.is_cancelled():
        chunk = fetch_next_chunk()
        if chunk is None:
            return NodeStatus.SUCCESS
        process(chunk)
    return NodeStatus.FAILURE   # cancelled
```

`token.is_set()` is an alias for `token.is_cancelled()`, provided for backward
compatibility with code that used a `threading.Event` as a stop signal.

## When to use AsyncActionNode

| Situation | Recommendation |
|-----------|---------------|
| Action completes in one tick | Plain `ActionNode.tick()` |
| Multi-tick action, no blocking I/O | `StatefulActionNode` |
| Blocking I/O, slow hardware, network calls | `AsyncActionNode` |
| True OS-level parallelism | `AsyncActionNode` with multiple nodes running concurrently |
