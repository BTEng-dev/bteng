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
    avg = stats.total_duration / stats.tick_count if stats.tick_count else 0.0
    print(f"{uid:30s}  ticks={stats.tick_count:4d}  "
          f"total={stats.total_duration:.4f}s  "
          f"avg={avg:.6f}s")

# Currently RUNNING nodes
print("Running nodes:", inspector.running_nodes())

# Full path from root to deepest running node
print("Active path:", inspector.active_path())
```

### NodeStats fields

| Field | Type | Meaning |
|-------|------|---------|
| `tick_count` | `int` | Total number of times this node was ticked |
| `success_count` | `int` | Ticks that returned SUCCESS |
| `failure_count` | `int` | Ticks that returned FAILURE |
| `total_duration` | `float` | Sum of all tick durations in seconds |
| `min_duration` | `float` | Fastest tick, in seconds |
| `max_duration` | `float` | Slowest tick, in seconds |

There is no `avg_duration` field — divide `total_duration` by `tick_count`, guarding
against a node that was never ticked. For the last status and tick time, read
`node.status` and `node.last_tick_time` on the node itself.

### Inspector.explanations()

`explanations()` returns an `ExplainEntry` for each recorded node event, useful for
post-run analysis:

```python
for entry in inspector.explanations():
    print(entry.node_name, entry.event, entry.reason, entry.timestamp)
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
logger.add_console_sink(colored=True)      # pretty-print to stdout
logger.add_json_file_sink("run.log")       # write newline-delimited JSON to a file
logger.add_custom_sink(lambda entry: ...)  # any callable taking a LogEntry
```

### LogEntry fields

| Field | Type | Meaning |
|-------|------|---------|
| `timestamp` | `float` | Unix timestamp |
| `node_name` | `str` | Node's name |
| `node_uid` | `str` | Node's unique ID |
| `old_status` | `NodeStatus` | Status before the transition |
| `new_status` | `NodeStatus` | Status after the transition |
| `duration` | `float` | How long the tick took, in seconds |
| `reason` | `str` | Why the transition happened, when the node supplied one |
| `level` | `LogLevel` | Severity level |
| `message` | `str` | Human-readable description |

---

## ExecutionTracer

`ExecutionTracer` records a `TraceFrame` at the end of every tick — including ticks
that changed nothing on the blackboard. Each frame holds the execution records of the
nodes that ran, plus a blackboard snapshot when the tick changed one. Use it for
replay, regression comparisons, and debugging non-deterministic failures.

```python
from bteng import ExecutionTracer, TreeExecutor

tracer = ExecutionTracer()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_tracer(tracer)
executor.tick_until_result()

# Inspect frames -- frames() is a method, not a property
for frame in tracer.frames():
    print(f"Tick {frame.tick_index}:")
    for record in frame.node_records:
        print(f"  {record.uid}: {record.old_status.value} -> {record.status.value}")
```

### TraceFrame fields

| Field | Type | Meaning |
|-------|------|---------|
| `tick_index` | `int` | Zero-based tick number |
| `timestamp` | `float` | `time.monotonic()` when the frame opened |
| `node_records` | `list[NodeExecutionRecord]` | One record per node that executed in this tick |
| `blackboard_snapshot` | `dict[str, str]` | Blackboard contents, present only when the tick changed them |

Each `NodeExecutionRecord` carries `uid`, `name`, `node_type`, `old_status`,
`status`, `duration`, `tick_time`, `feedback_message` and `halt_reason`.

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
print("Ticks:", tracer.frame_count())
for uid, stats in inspector.all_stats().items():
    avg = stats.total_duration / stats.tick_count if stats.tick_count else 0.0
    print(uid, stats.tick_count, f"{avg*1000:.2f}ms")
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
