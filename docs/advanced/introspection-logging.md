# Introspection and Logging

BTEng provides three complementary observability tools that can be attached to
`TreeExecutor` without modifying any node code.

| Tool | Purpose |
|------|---------|
| `Inspector` | Live per-node execution data: which nodes are active, tick counts, timing |
| `Logger` | Structured log of every node status transition |
| `ExecutionTracer` | Per-tick frame snapshots for replay and regression testing |

All three are optional. Attach whichever you need; the executor wires them together
automatically.

---

## Inspector

`Inspector` tracks which nodes are currently running, the active path from root to
the deepest running leaf, and per-node statistics.

```python
from bteng import Inspector, NodeStatus, TreeExecutor

inspector = Inspector.create()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_inspector(inspector)
executor.tick_until_result()

# Per-node stats
for uid, stats in inspector.all_stats().items():
    print(f"{uid:30s}  ticks={stats.tick_count:4d}  "
          f"total={stats.total_duration:.4f}s  "
          f"avg={stats.avg_duration:.6f}s")

# Active nodes at the last tick
print("Active nodes:", inspector.active_nodes())

# Full path from root to deepest running node
print("Active path:", inspector.active_path())
```

### NodeStats fields

| Field | Type | Meaning |
|-------|------|---------|
| `tick_count` | `int` | Total number of times this node was ticked |
| `total_duration` | `float` | Sum of all tick durations in seconds |
| `avg_duration` | `float` | `total_duration / tick_count` |
| `last_status` | `NodeStatus` | Status returned on the last tick |
| `last_tick_time` | `float` | Unix timestamp of the last tick |

### Inspector.explain()

`explain()` returns a human-readable execution trace entry for each node transition,
useful for post-run analysis:

```python
for entry in inspector.explain():
    print(entry.node_name, entry.transition, entry.timestamp)
```

---

## Logger

`Logger` records every node status transition to one or more sinks. Attach it to
`TreeExecutor` and it logs automatically.

```python
from bteng import LogLevel, Logger, TreeExecutor

logger = Logger.create()
logger.add_console_sink(colored=True)
logger.set_min_level(LogLevel.DEBUG)

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_logger(logger)
executor.tick_until_result()
```

### Log levels

| Level | When used |
|-------|-----------|
| `DEBUG` | Every tick, every node |
| `INFO` | Status transitions only |
| `WARNING` | Unexpected states (e.g., missing blackboard key) |
| `ERROR` | Validation failures, runtime errors |

### Log sinks

```python
logger.add_console_sink(colored=True)           # pretty-print to stdout
logger.add_json_file_sink("run.log")                 # write to a file
logger.add_sink(lambda entry: ...)              # custom callable sink
```

### LogEntry fields

| Field | Type | Meaning |
|-------|------|---------|
| `timestamp` | `float` | Unix timestamp |
| `node_name` | `str` | Node's name |
| `node_uid` | `str` | Node's unique ID |
| `status` | `NodeStatus` | Status at this transition |
| `level` | `LogLevel` | Severity level |
| `message` | `str` | Human-readable description |

---

## ExecutionTracer

`ExecutionTracer` records a `TraceFrame` at the end of every tick. Each frame is a
snapshot of every node's status at that point in time. Use it for replay, regression
comparisons, and debugging non-deterministic failures.

```python
from bteng import ExecutionTracer, TreeExecutor

tracer = ExecutionTracer()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_tracer(tracer)
executor.tick_until_result()

# Inspect frames
for i, frame in enumerate(tracer.frames):
    print(f"Tick {i}:")
    for uid, status in frame.node_statuses.items():
        print(f"  {uid}: {status}")
```

### TraceFrame fields

| Field | Type | Meaning |
|-------|------|---------|
| `tick_index` | `int` | Zero-based tick number |
| `timestamp` | `float` | Unix timestamp at end of tick |
| `node_statuses` | `dict[str, NodeStatus]` | Status of every node at this tick |
| `root_status` | `NodeStatus` | Status returned by the root node |

### Legacy transition events

`ExecutionTracer` also supports the older `log_transition()` API. `TreeExecutor` calls
`begin_frame()` and `end_frame()` automatically, so `TraceFrame` recording is opt-in
with no changes to node code.

---

## Attaching all three

When `Inspector` and `Logger` are both attached, `TreeExecutor` wires them together:
the logger receives a copy of every transition the inspector records.

```python
from bteng import ExecutionTracer, Inspector, LogLevel, Logger, TreeExecutor

inspector = Inspector.create()

logger = Logger.create()
logger.add_console_sink(colored=True)
logger.set_min_level(LogLevel.INFO)

tracer = ExecutionTracer()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_inspector(inspector)
executor.set_logger(logger)
executor.set_tracer(tracer)
executor.tick_until_result()

# Post-run analysis
print("Ticks:", len(tracer.frames))
for uid, stats in inspector.all_stats().items():
    print(uid, stats.tick_count, f"{stats.avg_duration*1000:.2f}ms")
```

---

## EventBus

`EventBus` provides application-level pub/sub. Nodes publish named events; application
code subscribes to them.

```python
from bteng import BehaviorEvent, EventBus, TreeExecutor

bus = EventBus.create()
bus.subscribe("goal_reached",  lambda e: print("Goal:", e.payload))
bus.subscribe("*",             lambda e: print("Any event:", e.name))

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_event_bus(bus)
executor.start_event_loop()
# ... your application runs ...
executor.stop_event_loop()
```

Nodes publish events by calling the bus directly, typically passed as a parameter via
`NodeConfig.params` or retrieved from the blackboard.

---

## Performance notes

- `Inspector` adds two dict lookups and a float subtraction per node tick. Overhead is
  negligible for trees with fewer than a few hundred nodes.
- `Logger` with `LogLevel.DEBUG` generates one log entry per node per tick. For high-
  frequency trees, use `LogLevel.INFO` to log transitions only.
- `ExecutionTracer` allocates one `TraceFrame` per tick. For very long-running
  sessions, call `tracer.clear()` periodically to release memory.
