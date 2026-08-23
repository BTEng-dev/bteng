# Asyncio Integration

BTEng ticks synchronously. Many host applications — an `asyncua` OPC UA server, an
aiohttp service, any `asyncio.run(main())` program — own an event loop and expose their
API as coroutines. `CoroActionNode` lets leaf nodes `await` those coroutines without
turning the tick loop into an asynchronous one.

The tree keeps its sync tick model. Only the leaf work moves to the loop.

---

## How it works

`AsyncioBridge` schedules a coroutine on an event loop with
`asyncio.run_coroutine_threadsafe()` and returns a `concurrent.futures.Future` — the
same object `AsyncActionNode` already polls each tick. So the bridge exposes the same
`submit()` contract as [`ThreadPool`](../reference/concurrency.md#threadpool), and no
core code changes:

1. First tick — the node submits its coroutine and returns `RUNNING`.
2. Later ticks — the node checks `future.done()` and returns `RUNNING` until it is.
3. Done — the coroutine's `NodeStatus` becomes the node's status.

`CoroActionNode` ignores the `ThreadPool` injected by `TreeExecutor`; coroutines belong
on the loop, not on worker threads.

---

## The two loop modes

**Attached** — the host owns the loop. The bridge never starts or stops it:

```python
import asyncio
from bteng import AsyncioBridge, set_default_bridge

async def main():
    set_default_bridge(AsyncioBridge(asyncio.get_running_loop()))
    ...

asyncio.run(main())
```

**Owned** — no loop given, so the bridge runs one on a daemon thread and stops it on
`shutdown()`. Useful for scripts and tests where the tree is the whole program:

```python
from bteng import AsyncioBridge

bridge = AsyncioBridge()      # starts its own loop thread
...
bridge.shutdown()
```

If a `CoroActionNode` ticks with no bridge configured at all, it lazily creates an
owned-loop default bridge. Call `shutdown_default_bridge()` to tear it down.

---

## Writing a coroutine node

Override `execute_async` with `async def`:

```python
from bteng import CancellationToken, CoroActionNode, InputPort, NodeStatus, OutputPort

class ReadTag(CoroActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("tag"), OutputPort("value")]

    async def execute_async(self, token: CancellationToken) -> NodeStatus:
        tag = self.get_input("tag", "temperature")
        value = await client.read(tag)        # host-application coroutine
        self.set_output("value", value)
        return NodeStatus.SUCCESS
```

For inline nodes, use `coro_action()`:

```python
from bteng import coro_action

async def _ping(node, token):
    await client.ping()
    return True                                # bool → SUCCESS / FAILURE

node = coro_action("Ping", _ping)
```

Bind a specific bridge per node with `node.set_bridge(bridge)`; otherwise the default
bridge is used.

---

## Driving the tick loop

The tick loop blocks, so it must not run on the event loop thread — that would stall the
host's I/O. Run it on a worker thread:

```python
status = await asyncio.to_thread(engine.run_until_complete, 200)
```

For a long-lived tree that ticks forever, the same shape applies:

```python
def _tick_forever(stop: threading.Event):
    while not stop.is_set():
        executor.tick_once()
        time.sleep(0.05)

await asyncio.to_thread(_tick_forever, stop)
```

If your ticks are genuinely fast (microseconds, all real work inside coroutine leaves),
you can instead tick from the loop directly and skip the thread:

```python
async def tick_forever(executor, period=0.05):
    while True:
        executor.tick_once()
        await asyncio.sleep(period)
```

Use this only when you are sure no node blocks — one blocking condition node freezes the
host application.

Halting is safe in this mode: when a decorator or reactive parent halts a running
`CoroActionNode` on the loop thread (a `Timeout` firing, a guard condition flipping), the
node cancels the task and returns immediately instead of blocking the loop it depends on.
The coroutine unwinds in the background and the orphan guard prevents a second copy from
starting before it finishes.

---

## The runner pattern

Driving the tick loop is one half. The other half is the *lifecycle*: an async coordinator
starts a tree, keeps serving its own traffic, and later either sees it finish or cancels it
— without ever being blocked by either.

Three rules make that work:

1. **`asyncio.create_task`, never `await` the tree inline** in a request handler.
2. **Cancel sets a flag.** It must not call `engine.halt()` — that would put the halt wait
   on the event loop, and two threads would touch the tree at once. The worker thread that
   owns the tree does the halting.
3. **Report status after `to_thread` returns**, so a natural finish and a cancellation come
   back through the same `await`.

```python
class BehaviorTreeRunner:
    def __init__(self, engine, period=0.05):
        self._engine, self._period = engine, period
        self._stop = threading.Event()
        self._task = None

    # -- both return immediately -------------------------------------------
    def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    def cancel(self):
        self._stop.set()                     # microseconds

    # -- internals ----------------------------------------------------------
    async def _run(self):
        result = await asyncio.to_thread(self._tick_loop)
        await self.on_finished(result)       # your state machine transition
        return result

    def _tick_loop(self):                    # worker thread; blocking is fine
        while True:
            if self._stop.is_set():
                self._engine.halt()          # blocks THIS thread, not the loop
                return "CANCELLED"
            status = self._engine.tick_once()
            if status != NodeStatus.RUNNING:
                return "COMPLETED" if status == NodeStatus.SUCCESS else "FAILED"
            time.sleep(self._period)
```

A cancel that takes two seconds to unwind costs the worker thread two seconds. The
coordinator keeps serving throughout:

| t | event loop | worker thread |
|---|---|---|
| 0.00 | `cancel()` returns, keeps serving | mid-tick |
| 0.05 | still serving | sees the flag → `halt()` |
| 0.05–2.0 | **still serving** | coroutines unwind |
| 2.00 | `await` resumes → state machine → `CANCELLED` | thread exits |

The code above is complete and runnable as written — the heartbeat prints prove the host
loop is never blocked while the tree ticks.

---

## Cancellation

Halting a `CoroActionNode` is cooperative first, forced second:

1. The `CancellationToken` is cancelled. Poll `token.is_cancelled()` **between awaits**
   and return early.
2. If the coroutine does not finish within `HALT_GRACE` (default `0.2` s), the underlying
   asyncio task is cancelled, raising `asyncio.CancelledError` inside it, and a warning is
   logged naming the node.
3. `halt()` waits for the coroutine to unwind, so `finally` blocks and cleanup complete
   before the tick loop continues.

The halting thread blocks for that grace period, and it is usually the tick thread — so a
node that ignores the token makes the whole tree deaf for `HALT_GRACE` seconds (at 20 Hz,
`0.2` s is 4 missed ticks). The warning exists so those nodes show up in the log instead
of quietly eating ticks:

```text
WARNING bteng.nodes.leaf.coro_action: CoroActionNode 'ReadTag' did not stop within
HALT_GRACE (0.2s) — forcing asyncio cancellation. Poll token.is_cancelled() between
awaits to halt promptly.
```

```python
class LongPoll(CoroActionNode):
    HALT_GRACE = 0.5                       # cleanup awaits — give it more room

    async def execute_async(self, token: CancellationToken) -> NodeStatus:
        try:
            while not token.is_cancelled():
                item = await queue.get()
                if item is SENTINEL:
                    return NodeStatus.SUCCESS
            return NodeStatus.FAILURE
        except asyncio.CancelledError:
            await self._cleanup()          # forced path — still runs before unwinding
            raise
```

A coroutine cancelled this way ticks as `FAILURE`.

`shutdown()` applies the same discipline to the bridge itself: on an **owned** loop it
cancels every in-flight coroutine and awaits it, so `finally` blocks run instead of the
tasks being destroyed while pending. On an **attached** loop it only stops accepting new
work — the host owns those tasks and its own shutdown sequence.

---

## Liveness and failure reporting

A behavior tree must never wedge silently, so the bridge reports its own health and the
node turns an unusable bridge into a status rather than an exception:

```python
bridge.is_alive()      # False once shut down, loop closed/stopped, or loop thread died
bridge.loop_error      # the BaseException that tore down an owned loop, if any
```

`submit()` raises `RuntimeError` — which `CoroActionNode.tick()` converts to `FAILURE`
with an explanatory `feedback_message` — when the bridge is shut down, the loop is
closed, the owned loop thread died (a `SystemExit` inside a coroutine will do that), or
an attached loop is not running.

> [!WARNING]
> **Attach after the loop is running**
>
> An attached loop that the host has **stopped** is indistinguishable from one that
> has not **started**, so both are rejected. Build attached bridges from inside the
> running loop — `AsyncioBridge.from_running_loop()` or
> `AsyncioBridge(asyncio.get_running_loop())`.

A node pinned with `set_bridge()` fails rather than migrating to another loop if that
bridge dies; only unpinned nodes fall back to the default bridge. Running host I/O on a
loop the host does not own is worse than reporting `FAILURE`.

Two further guards:

- `wait_all()` raises `RuntimeError` when called from the loop thread — it would block the
  very loop that has to finish the work. `halt()` does not raise there; it degrades to a
  non-blocking cancel (see above).
- A coroutine whose cancellation cleanup itself awaits may outlive `halt()`. The node
  keeps the handle and returns `FAILURE` ("previous coroutine still unwinding") on
  subsequent ticks rather than starting a second concurrent copy.

---

## Full example

An asyncio host, a device exposed as coroutines, and a tree ticked off the loop:

```python
import asyncio
from bteng import (
    AsyncioBridge, BehaviorTreeEngine, Blackboard, CoroActionNode,
    NodeConfig, NodeStatus, SequenceNode, set_default_bridge,
)

class Connect(CoroActionNode):
    async def execute_async(self, token) -> NodeStatus:
        await device.connect()
        return NodeStatus.SUCCESS

async def main():
    set_default_bridge(AsyncioBridge(asyncio.get_running_loop()))

    bb = Blackboard()
    tree = SequenceNode("root", children=[Connect("Connect", NodeConfig(blackboard=bb))])
    engine = BehaviorTreeEngine(tree, blackboard=bb, hz=20.0)

    status = await asyncio.to_thread(engine.run_until_complete, 200)
    print(status)

asyncio.run(main())
```

The snippet above runs as written; add `provided_ports()` to the node if you want the
URL and timeout to come from the blackboard rather than being hard-coded.

---

## Choosing between the node types

| Node | Work | Runs on |
|------|------|---------|
| `StatefulActionNode` | Splittable across ticks, non-blocking | Tick thread |
| `AsyncActionNode` | Blocking calls, CPU work | `ThreadPool` worker |
| `CoroActionNode` | `async def` APIs | Event loop |

Mixing them in one tree is fine — each resolves its own execution context.

> [!WARNING]
> Never call `bridge.wait_all()` from the loop thread. It blocks until pending
> coroutines finish, which the loop itself must run — that deadlocks.
