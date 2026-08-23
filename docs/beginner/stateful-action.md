# Stateful Actions

Most simple actions either succeed or fail in a single tick. But real-world tasks —
navigating to a point, waiting for a sensor, downloading a file — take many ticks to
complete. `StatefulActionNode` provides a clean three-phase lifecycle for exactly this
case.

---

## Why not plain `ActionNode`?

With a plain `ActionNode`, you have to track multi-tick state yourself:

```python
class Navigate(ActionNode):
    def __init__(self, name):
        super().__init__(name)
        self._started = False
        self._progress = 0.0

    def tick(self):
        if not self._started:
            self._started = True
            self._progress = 0.0
        self._progress += 0.1
        if self._progress >= 1.0:
            self._started = False   # reset for next activation
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING
```

This gets messy quickly. You have to manually reset state, handle interruptions, and
remember what was initialised.

`StatefulActionNode` handles all of that for you.

---

## The three-phase lifecycle

```
First tick of this activation
          │
          ▼
      on_start()  ──────────────────────────────────────────────────┐
          │                                                          │
          ▼ (returns RUNNING)                                        │
      on_running() ← called on every subsequent tick                 │
          │                                                          │
          ├── returns SUCCESS or FAILURE ──► node is done            │
          │                                                          │
          └── returns RUNNING ──► wait for next tick                 │
                                                                     │
         halted externally (parent interrupted)                      │
                    │                                                 │
                    ▼                                                 │
              on_halted()  ◄────────────────────────────────────────┘
```

| Method | Called when | Return value |
|--------|-------------|--------------|
| `on_start()` | First tick of each activation | `NodeStatus` (usually `RUNNING`) |
| `on_running()` | Every subsequent tick while `RUNNING` | `NodeStatus` |
| `on_halted()` | Node is interrupted by parent | `None` |

`StatefulActionNode` resets automatically between activations. You do not need to
reset `_started` flags — just initialise state in `on_start()`.

---

## Minimal example

```python
from bteng import NodeStatus, StatefulActionNode, Tree, TreeExecutor, TreeMetadata

class CountToThree(StatefulActionNode):
    def on_start(self):
        self.count = 0
        print("Starting count")
        return NodeStatus.RUNNING

    def on_running(self):
        self.count += 1
        print(f"Tick {self.count}")
        if self.count >= 3:
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING

    def on_halted(self):
        print("Interrupted — cleaning up")

tree = Tree(TreeMetadata(id="counter"), CountToThree("counter"))
executor = TreeExecutor()
executor.set_tree(tree)

for _ in range(5):
    status = executor.tick_once()
    print("Status:", status)
    if status != NodeStatus.RUNNING:
        break
```

Expected output:

```text
Starting count
Tick 1
Status: NodeStatus.RUNNING
Tick 2
Status: NodeStatus.RUNNING
Tick 3
Status: NodeStatus.SUCCESS
```

`on_start()` runs only once. `on_running()` runs on ticks 2 and 3. The node exits on
`SUCCESS` without ever needing to reset itself.

---

## Handling interruption with `on_halted`

When a parent node (such as a `ReactiveSequenceNode`) halts a child mid-task,
`on_halted()` is called. Use it to release resources, cancel requests, or log
diagnostics.

```python
class SendRequest(StatefulActionNode):
    def on_start(self):
        self._request_id = send_async_request(self.get_input("endpoint"))
        return NodeStatus.RUNNING

    def on_running(self):
        if is_complete(self._request_id):
            self.set_output("response", get_result(self._request_id))
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING

    def on_halted(self):
        cancel_request(self._request_id)   # don't leave dangling requests
```

If you do not override `on_halted()`, the base class provides a no-op default. Override
it whenever your action allocates external resources.

---

## Practical example with ports and blackboard

```python
from bteng import (
    ActionNode, Blackboard, InputPort, NodeConfig, NodeStatus,
    OutputPort, StatefulActionNode, Tree, TreeBuilder, TreeExecutor, TreeMetadata,
    register_node,
)

@register_node("Navigate")
class Navigate(StatefulActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("goal",        description="Target (x, y)"),
            OutputPort("status_msg", description="Human-readable outcome"),
        ]

    def on_start(self):
        self._goal = self.get_input("goal", (0.0, 0.0))
        self._ticks_needed = 3
        self._elapsed = 0
        return NodeStatus.RUNNING

    def on_running(self):
        self._elapsed += 1
        if self._elapsed >= self._ticks_needed:
            self.set_output("status_msg", f"Arrived at {self._goal}")
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING

    def on_halted(self):
        self.set_output("status_msg", "Navigation cancelled")

bb = Blackboard.create("nav_demo")
bb.set("goal", (5.0, 3.0))

tree = (
    TreeBuilder(blackboard=bb)
    .tree_id("NavigationDemo")
    .node("Navigate", "Nav")
        .map("goal", "goal")
        .map_output("status_msg", "nav_result")
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
status = executor.tick_until_result(max_ticks=10)

print(status)
print(bb.get("nav_result"))
```

Expected output:

```text
NodeStatus.SUCCESS
Arrived at (5.0, 3.0)
```

---

## When to use which action type

| Situation | Use |
|-----------|-----|
| Finishes in one tick | `ActionNode` with a plain `tick()` |
| Spans multiple ticks, runs synchronously | `StatefulActionNode` |
| Blocking I/O, hardware calls, slow network | `AsyncActionNode` |
| Simple glue logic with no state | Inline lambda |

`StatefulActionNode` is the right choice for most real tasks. `AsyncActionNode` is for
cases where the work itself must run in a background thread so the tick loop is not
blocked.

---

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Initialising state in `__init__` instead of `on_start()` | State set in `__init__` is shared across all activations. Always reset in `on_start()` |
| Returning `None` from `on_start()` | `on_start()` must return a `NodeStatus`. Returning `None` will cause a runtime error |
| Not overriding `on_halted()` when resources were acquired | Resources leak if the node is interrupted. Always override `on_halted()` when needed |
| Returning `RUNNING` from `on_start()` when work is already done | Check for trivial completion in `on_start()` and return `SUCCESS` directly if so |

Next: [Blackboard basics](blackboard-basics.md).
