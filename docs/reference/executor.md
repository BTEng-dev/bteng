# Executor & Engine

BTEng provides two execution APIs. `TreeExecutor` is the preferred choice for new code;
`BehaviorTreeEngine` is kept for backward compatibility.

---

## TreeExecutor

Full-featured executor with event loop, pause/resume, Inspector, Logger, and EventBus.

```python
from bteng import TreeExecutor, ExecutorConfig

executor = TreeExecutor(ExecutorConfig(
    tick_interval=0.02,     # seconds between ticks
    thread_pool_size=4,     # workers for AsyncActionNode
    halt_on_completion=True,
))
```

### Setup

Setup order does not matter — `set_tree()`, `set_inspector()`, `set_logger()`, and
`set_thread_pool()` can be called in any order. The executor wires everything lazily.

```python
executor.set_tree(tree)         # validates ports automatically
executor.set_inspector(inspector)
executor.set_logger(logger)     # auto-wired to inspector if both set
executor.set_event_bus(bus)
executor.set_tracer(tracer)
```

`set_tree()` always calls `tree.validate()`. Raises `TreeValidationError` if port
declarations are misconfigured.

### Manual tick mode

```python
status = executor.tick_once()                    # one tick → NodeStatus
status = executor.tick_until_result(max_ticks=1000)
```

### Event loop mode (background thread)

```python
executor.start_event_loop()
executor.on_completion(lambda s: print("Done:", s))

# ... tree runs in background ...

executor.pause()
executor.resume()
executor.halt_tree()
executor.reset_tree()
executor.stop_event_loop()
```

### Full API

| Method | Description |
|--------|-------------|
| `set_tree(tree)` | Attach tree, validate ports, inject thread pool |
| `tick_once()` | Run one tick, return `NodeStatus` |
| `tick_until_result(max_ticks)` | Tick until `SUCCESS` or `FAILURE` |
| `start_event_loop()` | Start background tick thread |
| `stop_event_loop()` | Stop background tick thread |
| `pause()` | Suspend ticking (event loop mode) |
| `resume()` | Resume ticking |
| `halt_tree()` | Halt all running nodes |
| `reset_tree()` | Reset all nodes to IDLE |
| `on_completion(cb)` | Register callback fired on tree completion |

---

## BehaviorTreeEngine (legacy)

Simpler API; kept for backward compatibility. Prefer `TreeExecutor` for new code.

```python
from bteng import BehaviorTreeEngine

engine = BehaviorTreeEngine(
    root,
    blackboard=bb,    # optional; creates global bb if omitted
    tracer=tracer,    # optional ExecutionTracer
    hz=10.0,          # optional tick rate
)

engine.tick_once()
engine.run_until_complete(max_ticks=1000, interval=0.05)
engine.halt()

# Properties
engine.tick_count
engine.blackboard
engine.root
```

### From XML

```python
engine = BehaviorTreeEngine.from_xml("my_tree.xml", blackboard=bb, hz=10.0)
status = engine.run_until_complete()
```

---

## EventBus

Pub-sub for named `BehaviorEvent` objects. Attach to `TreeExecutor` for tree-level
event routing.

```python
from bteng import EventBus, BehaviorEvent

bus = EventBus.create()

# Subscribe to named events
bus.subscribe("goal_reached", lambda e: print("Goal:", e.payload))

# Wildcard — receives all events
bus.subscribe("*", lambda e: print(e.name, e.payload))

executor.set_event_bus(bus)
```

Nodes publish events by holding a reference to the bus (passed via `NodeConfig.params`):

```python
bus.publish(BehaviorEvent("goal_reached", payload={"pos": (1, 2)}))
```

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `ExecutorConfig` | dataclass | `bteng.core.executor` | [executor.py](../../bteng/core/executor.py) |
| `TreeExecutor` | class | `bteng.core.executor` | [executor.py](../../bteng/core/executor.py) |
| `BehaviorEvent` | dataclass | `bteng.core.executor` | [executor.py](../../bteng/core/executor.py) |
| `EventBus` | class | `bteng.core.executor` | [executor.py](../../bteng/core/executor.py) |
| `BehaviorTreeEngine` | class | `bteng.core.engine` | [engine.py](../../bteng/core/engine.py) |
