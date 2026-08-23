# BTEng Node System

## Base Classes

### `TreeNode`

All nodes inherit from `TreeNode`. Key interface:

```python
node.execute_tick() -> NodeStatus   # called by engine / parents
node.halt()                         # stop & reset to IDLE
node.reset_node()                   # unconditional recursive reset to IDLE
node.get_input(port, default)       # read from blackboard or params
node.set_output(port, value)        # write to blackboard
node.status                         # current NodeStatus
node.config                         # NodeConfig — ports, params, blackboard ref
node.blackboard                     # shortcut to config.blackboard
node.tick_count                     # total execute_tick() calls
node.last_tick_duration             # wall-clock duration of last tick (seconds)
node.failure_reason                 # string set via set_failure_reason()
```

Override in subclasses:

```python
def tick(self) -> NodeStatus: ...           # required
def _on_halt(self): ...                     # optional cleanup when halted
def _on_reset(self): ...                    # optional reset hook (called by reset_node)

@classmethod
def provided_ports(cls) -> List[PortDefinition]: ...  # optional port declarations
```

> **Important:** `self._status` inside `tick()` always reflects the **previous** tick's
> result, not the current one. `execute_tick()` sets `self._status` only after `tick()`
> returns. This is intentional — it lets nodes detect first-entry vs. re-entry without
> extra flags.

---

## Control Nodes

### `SequenceNode`

Ticks children left-to-right, resuming from where it left off between ticks.

| Child returns | Sequence returns |
|---------------|-----------------|
| SUCCESS       | advance to next  |
| RUNNING       | RUNNING (resume next tick) |
| FAILURE       | FAILURE (halt all children) |
| all SUCCESS   | SUCCESS |

### `FallbackNode` (Selector)

Inverse of Sequence.

| Child returns | Fallback returns |
|---------------|-----------------|
| FAILURE       | advance to next  |
| RUNNING       | RUNNING (resume next tick) |
| SUCCESS       | SUCCESS (halt remaining) |
| all FAILURE   | FAILURE |

### `ParallelNode`

Ticks **all** children every tick (in the same thread, sequentially).

Parameters:
- `success_threshold`: how many must succeed. Any value `<= 0` (default `-1`)
  means *all children*, recomputed each tick. A value above the child count is
  clamped down to it. Also a declared input port, re-read every tick, so a
  programmatic remap (`NodeConfig(input_ports={"success_threshold": "max_ok"})`)
  works — XML cannot use a `{ref}` here, since control-node attributes are
  constructor arguments resolved at build time.
- `failure_threshold`: how many must fail to abort (`1` = any, default). Clamped
  into `[1, child count]`.
- `policy`: `ParallelPolicy` enum — when provided it fixes **both** thresholds:

| Policy | Thresholds | Meaning |
|---|---|---|
| `REQUIRE_ALL_SUCCESS` | success = n, failure = 1 | SUCCESS only if every child succeeds; the first failure aborts |
| `REQUIRE_ONE_SUCCESS` | success = 1, failure = n | SUCCESS as soon as any child succeeds. A failing child does **not** abort — the others keep running. FAILURE only once every child has failed |
| `REQUIRE_ALL_COMPLETE` | success = `success_threshold` (or n), failure = n | Nothing is decided, and no running child is halted, until every child has finished |

With no children the node returns SUCCESS under every policy: "all children
succeeded" is vacuously true, matching `SequenceNode([])`.

```python
from bteng import ParallelNode, ParallelPolicy

p = ParallelNode("p", children=[...], policy=ParallelPolicy.REQUIRE_ONE_SUCCESS)
```

Once a child reaches SUCCESS or FAILURE it is not re-ticked; its result is accumulated
until Parallel itself terminates.

### `ReactiveSequenceNode`

Like Sequence, but **restarts from child[0] every tick**. Earlier conditions can
interrupt a later running action when they change.

### `ReactiveFallbackNode`

Like Fallback, but **restarts from child[0] every tick**. Higher-priority conditions
can interrupt a running lower-priority action.

---

## Decorator Nodes

Each decorator wraps exactly one child. `TreeBuilder` raises `RuntimeError` at build
time if a decorator scope is closed (`end()`) without a child having been added.

| Decorator | Behaviour |
|-----------|-----------|
| `Inverter` | Flip SUCCESS ↔ FAILURE, pass RUNNING |
| `Retry(max_attempts)` | Re-tick child on FAILURE up to N times, returns RUNNING between attempts |
| `Timeout(duration)` | Return FAILURE if child exceeds `duration` seconds |
| `RateController(hz)` | Rate-limit child ticking, return cached status between ticks |
| `ForceSuccess` | Always return SUCCESS (unless child is RUNNING) |
| `ForceFailure` | Always return FAILURE (unless child is RUNNING) |

---

## Leaf Nodes

### `ActionNode`

Override `tick()`:

```python
from bteng import ActionNode, NodeStatus, InputPort

class MyAction(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("speed", default="1.0")]

    def tick(self) -> NodeStatus:
        speed = self.get_input("speed")   # "1.0" if not mapped in XML/builder
        # do work
        return NodeStatus.SUCCESS
```

### `ConditionNode`

Same API as `ActionNode`. Semantic distinction only — returns SUCCESS or FAILURE,
never RUNNING.

### `StatefulActionNode`

Three-phase lifecycle for long-running actions:

```python
from bteng import StatefulActionNode, NodeStatus

class MyTask(StatefulActionNode):
    def on_start(self) -> NodeStatus:    # called on first tick of each activation
        self._work = start_work()
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:  # called on subsequent ticks while RUNNING
        if self._work.done():
            return NodeStatus.SUCCESS
        return NodeStatus.RUNNING

    def on_halted(self) -> None:         # called when the node is halted externally
        self._work.cancel()
```

### `AsyncActionNode`

Runs `execute_async()` in a background thread. The main tick loop returns RUNNING
immediately and polls the result each tick.

```python
import time
from bteng import AsyncActionNode, CancellationToken, NodeStatus

class SlowScan(AsyncActionNode):
    def execute_async(self, token: CancellationToken) -> NodeStatus:
        for i in range(10):
            if token.is_cancelled():   # cooperative cancellation
                return NodeStatus.FAILURE
            time.sleep(0.1)
        return NodeStatus.SUCCESS
```

`token.is_set()` is an alias for `token.is_cancelled()` for backwards compatibility
with code written against the old `threading.Event` interface.

**Thread pool:** `TreeExecutor` automatically injects a shared `ThreadPool` into every
`AsyncActionNode` in the tree when `set_tree()` or `set_thread_pool()` is called.
Manual injection via `node.set_thread_pool(pool)` still works but is no longer required.

### Functional API (inline nodes)

```python
from bteng import action, condition, NodeStatus

is_ready = condition("is_ready", lambda: blackboard.get("ready"))
move     = action("move",        lambda: NodeStatus.SUCCESS)
```

Lambda receives no arguments. Return value is coerced:
- `NodeStatus` → passed through
- truthy → `SUCCESS`, falsy → `FAILURE`

---

## Port System

Ports declare what data a node reads from and writes to the Blackboard.

```python
from bteng import ActionNode, NodeStatus, InputPort, OutputPort, BidirectionalPort

class MyAction(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("target", description="Goal position", default="origin"),
            OutputPort("result", description="Outcome string"),
            BidirectionalPort("counter"),   # both read and written
        ]

    def tick(self):
        target = self.get_input("target")       # reads bb[input_ports["target"]]
                                                # or returns default if not mapped
        self.set_output("result", "done")       # writes bb[output_ports["result"]]
        return NodeStatus.SUCCESS
```

**`InputPort` defaults** are applied at parse time — if the XML attribute is absent,
the declared default is used. Override is always possible:

```xml
<!-- uses declared default "origin" -->
<Action ID="MyAction"/>

<!-- overrides default -->
<Action ID="MyAction" target="{current_goal}"/>
<Action ID="MyAction" target="fixed_pos"/>
```

Port remapping in XML:

```xml
<Action ID="MyAction" target="{goal}" result="{outcome}"/>
<!-- "target" reads/writes blackboard key "goal"  -->
<!-- "result" reads/writes blackboard key "outcome" -->
```

---

## Registration

```python
from bteng import register_node, NodeFactory

@register_node()           # registers as "MyAction"
class MyAction(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("speed", default="1.0")]

@register_node("alias")    # registers as "alias"
class Another(ActionNode):
    ...

# Or manually:
NodeFactory.get_instance().register(MyAction)
```

The factory reads `provided_ports()` to build the node manifest used by the XML parser,
`export_node_models_xml()`, and IDE tooling. Always use `provided_ports()` — **not**
`define_ports()` (incorrect alias that the factory does not read).

---

## Inspector integration

Nodes automatically report tick events to the `Inspector` when one is attached by the
executor. No node-level code changes are needed.

```python
executor.set_inspector(inspector)
# → inspector.on_node_tick() called after every execute_tick()
# → inspector.subscribe() callbacks fire with NodeExecutionRecord
```

`NodeExecutionRecord` fields:

| Field | Type | Description |
|-------|------|-------------|
| `uid` | `str` | Unique node ID |
| `name` | `str` | Node name |
| `node_type` | `NodeType` | Action, Control, etc. |
| `old_status` | `NodeStatus` | Status before this tick |
| `status` | `NodeStatus` | Status after this tick |
| `tick_time` | `float` | `time.monotonic()` timestamp |
| `duration` | `float` | Wall-clock tick duration (seconds) |
| `failure_reason` | `str` | Set via `set_failure_reason()` |

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `NodeStatus` | enum | `bteng.core.node` | [node.py](../../bteng/core/node.py) |
| `NodeConfig` | dataclass | `bteng.core.node` | [node.py](../../bteng/core/node.py) |
| `NodeContract` | dataclass | `bteng.core.node` | [node.py](../../bteng/core/node.py) |
| `TreeNode` | class | `bteng.core.node` | [node.py](../../bteng/core/node.py) |
| `ActionNode` | class | `bteng.nodes.leaf.action` | [action.py](../../bteng/nodes/leaf/action.py) |
| `ConditionNode` | class | `bteng.nodes.leaf.condition` | [condition.py](../../bteng/nodes/leaf/condition.py) |
| `StatefulActionNode` | class | `bteng.nodes.leaf.stateful_action` | [stateful_action.py](../../bteng/nodes/leaf/stateful_action.py) |
| `AsyncActionNode` | class | `bteng.nodes.leaf.async_action` | [async_action.py](../../bteng/nodes/leaf/async_action.py) |
| `PortDefinition` | dataclass | `bteng.core.node` | [node.py](../../bteng/core/node.py) |
| `SequenceNode` | class | `bteng.nodes.control.sequence` | [sequence.py](../../bteng/nodes/control/sequence.py) |
| `FallbackNode` | class | `bteng.nodes.control.fallback` | [fallback.py](../../bteng/nodes/control/fallback.py) |
| `ParallelNode` | class | `bteng.nodes.control.parallel` | [parallel.py](../../bteng/nodes/control/parallel.py) |
| `ParallelPolicy` | enum | `bteng.nodes.control.parallel` | [parallel.py](../../bteng/nodes/control/parallel.py) |
| `ReactiveSequenceNode` | class | `bteng.nodes.control.reactive_sequence` | [reactive_sequence.py](../../bteng/nodes/control/reactive_sequence.py) |
| `ReactiveFallbackNode` | class | `bteng.nodes.control.reactive_fallback` | [reactive_fallback.py](../../bteng/nodes/control/reactive_fallback.py) |
| `Inverter` | class | `bteng.nodes.decorators.inverter` | [inverter.py](../../bteng/nodes/decorators/inverter.py) |
| `Retry` | class | `bteng.nodes.decorators.retry` | [retry.py](../../bteng/nodes/decorators/retry.py) |
| `Timeout` | class | `bteng.nodes.decorators.timeout` | [timeout.py](../../bteng/nodes/decorators/timeout.py) |
| `RateController` | class | `bteng.nodes.decorators.rate_controller` | [rate_controller.py](../../bteng/nodes/decorators/rate_controller.py) |
| `ForceSuccess` | class | `bteng.nodes.decorators.force_result` | [force_result.py](../../bteng/nodes/decorators/force_result.py) |
| `ForceFailure` | class | `bteng.nodes.decorators.force_result` | [force_result.py](../../bteng/nodes/decorators/force_result.py) |
