# Concurrency

BTEng supports non-blocking execution via `AsyncActionNode`, cooperative cancellation
via `CancellationToken`, and a shared `ThreadPool` that is auto-injected by the executor.

---

## AsyncActionNode

Runs `execute_async()` in a background thread. The main tick loop returns `RUNNING`
immediately and polls the thread result on each subsequent tick.

```python
import time
from bteng import AsyncActionNode, CancellationToken, NodeStatus

class SlowScan(AsyncActionNode):
    def execute_async(self, token: CancellationToken) -> NodeStatus:
        for i in range(10):
            if token.is_cancelled():    # cooperative cancellation
                return NodeStatus.FAILURE
            time.sleep(0.1)
        return NodeStatus.SUCCESS
```

Check cancellation frequently in long loops. When the node is halted (e.g. by a
reactive parent), the token is cancelled and the background thread should exit cleanly.

### Backward compatibility

`token.is_set()` is an alias for `token.is_cancelled()` — code written against the old
`threading.Event` interface continues to work.

---

## CancellationToken

```python
from bteng import CancellationToken

token = CancellationToken()
token.is_cancelled()     # False initially
token.cancel()
token.is_cancelled()     # True
token.is_set()           # alias for is_cancelled()
```

Tokens are created internally by `AsyncActionNode`; you rarely need to construct them
directly.

---

## ThreadPool

`TreeExecutor` builds a shared `ThreadPool` and injects it into every `AsyncActionNode`.
No manual wiring is required.

```python
from bteng import TreeExecutor, ExecutorConfig

executor = TreeExecutor(ExecutorConfig(thread_pool_size=4))
executor.set_tree(tree)
executor.tick_once()       # pool created here, injected into all AsyncActionNodes
```

The pool is created on the first tick, and only if the tree actually contains a node that
wants one — a tree of plain synchronous nodes never pays for idle worker threads, and
`CoroActionNode` declines it outright. `thread_pool_size=0` disables it entirely; those
nodes then fall back to spawning a daemon thread each.

`executor.shutdown()` disposes the pool it built. Ticking again builds a fresh one.

> [!WARNING]
> **A bounded pool can starve**
>
> The pool has `thread_pool_size` workers. If a `ParallelNode` holds more
> `AsyncActionNode` children than that, the extras **queue** — they still run, just
> later, as workers free up.
>
> That is only a problem in one shape: an async node that blocks waiting on *another
> async node in the same tree*. If the waiter occupies the last worker, the node it
> waits for can never be scheduled, and the wait times out. Size the pool above your
> widest parallel branch, or set `thread_pool_size=0` so each node gets its own thread.

Manual injection still works for advanced use cases — a pool you pass in stays yours, and
`shutdown()` leaves it running:

```python
from bteng import ThreadPool

pool = ThreadPool(num_threads=8)
node.set_thread_pool(pool)
```

---

## CoroActionNode

Runs `execute_async()` as a coroutine on an asyncio event loop instead of a worker
thread. Use it when the work is already `async def` — an `asyncua` client, an aiohttp
session, any coroutine API.

```python
import asyncio
from bteng import AsyncioBridge, CancellationToken, CoroActionNode, NodeStatus, set_default_bridge

class FetchStatus(CoroActionNode):
    async def execute_async(self, token: CancellationToken) -> NodeStatus:
        if token.is_cancelled():
            return NodeStatus.FAILURE
        await client.fetch()
        return NodeStatus.SUCCESS

async def main():
    set_default_bridge(AsyncioBridge(asyncio.get_running_loop()))
    ...
```

The `ThreadPool` injected by `TreeExecutor` is ignored — coroutines run on the loop.
Halting cancels the token first and, after `HALT_GRACE` seconds (default `0.2`), cancels
the asyncio task and waits for it to unwind — logging a warning, since the halting thread
stalled for the whole grace period.

`FunctionCoroAction` / `coro_action(name, fn)` wrap a coroutine function as a node.

See [Asyncio integration](../advanced/asyncio-integration.md) for the full picture.

---

## AsyncioBridge

Schedules coroutines on an event loop and returns a `concurrent.futures.Future` — the
same `submit()` contract as `ThreadPool`.

```python
from bteng import AsyncioBridge, set_default_bridge, shutdown_default_bridge

bridge = AsyncioBridge(loop)     # attached: shutdown() leaves the loop running
bridge = AsyncioBridge()         # owned: runs a loop on a daemon thread

set_default_bridge(bridge)       # used by CoroActionNodes with no explicit bridge
shutdown_default_bridge()        # tear the default down
```

| Method | Purpose |
|--------|---------|
| `submit(fn, *args)` | Schedule `fn(*args)`; `fn` must return a coroutine. Raises `RuntimeError` if the bridge is unusable |
| `cancel_task(future)` | Hard-cancel the coroutine behind a submitted future (works before it starts too) |
| `task_done(future)` | True once the coroutine itself finished, not just the Future |
| `is_alive()` / `loop_error` | Bridge health; the exception that killed an owned loop |
| `pending_tasks()` | Count of in-flight coroutines |
| `wait_all(timeout)` | Block until pending coroutines finish (never call from the loop thread) |
| `shutdown(timeout=5.0)` | Stop accepting work; drain and stop the loop only if owned |

Module-level helpers: `set_default_bridge()`, `get_default_bridge()` (creates an
owned-loop bridge on first use), `shutdown_default_bridge()`.

---

## Concurrency model

| Mechanism | Use case |
|-----------|----------|
| `AsyncActionNode` + `ThreadPool` | Non-blocking leaf nodes; pool auto-injected |
| `CoroActionNode` + `AsyncioBridge` | Leaf nodes awaiting coroutine APIs |
| `CancellationToken` | Cooperative cancellation of async tasks |
| `ParallelNode` | Conceptual parallelism — ticks all children in the same thread |
| `Blackboard` (`threading.RLock`) | Safe cross-thread data sharing |
| `Inspector` (`threading.Lock`) | Thread-safe event collection |
| `ZmqPublisher` background thread | Decoupled event streaming; never blocks tick loop |

> [!NOTE]
> `ParallelNode` ticks all children within the **same thread** in sequence — it is not
> OS-level parallelism. Use `AsyncActionNode` for true concurrency.

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `CancellationToken` | class | `bteng.concurrency.cancellation_token` | [cancellation_token.py](../../bteng/concurrency/cancellation_token.py) |
| `ThreadPool` | class | `bteng.concurrency.thread_pool` | [thread_pool.py](../../bteng/concurrency/thread_pool.py) |
| `AsyncioBridge` | class | `bteng.concurrency.asyncio_bridge` | [asyncio_bridge.py](../../bteng/concurrency/asyncio_bridge.py) |
| `CoroActionNode` | class | `bteng.nodes.leaf.coro_action` | [coro_action.py](../../bteng/nodes/leaf/coro_action.py) |
