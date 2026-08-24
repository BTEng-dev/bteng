# Introspection

BTEng provides three observability tools: `Inspector` (live node stats and active path),
`Logger` (structured state-transition log), and `ExecutionTracer` (per-tick frame
recorder for replay and regression).

---

## Inspector

Collects per-node tick statistics and maintains a real-time view of which nodes are
currently `RUNNING`.

```python
from bteng import Inspector, TreeExecutor

inspector = Inspector.create()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_inspector(inspector)   # injects inspector into every node
executor.tick_until_result()

# Per-node statistics
for uid, stats in inspector.all_stats().items():
    print(uid, stats.tick_count, f"{stats.total_duration*1000:.1f}ms")

# Currently RUNNING nodes
print(inspector.running_nodes())

# Full path from root to the deepest RUNNING node
print(inspector.active_path())

# Execution history (most recent first)
for record in inspector.execution_history(max_entries=100):
    print(record.name, record.old_status.value, "→", record.status.value)
```

### NodeExecutionRecord fields

| Field | Type | Description |
|-------|------|-------------|
| `uid` | `str` | Unique node ID |
| `name` | `str` | Node name |
| `node_type` | `NodeType` | Action, Control, Decorator, etc. |
| `old_status` | `NodeStatus` | Status before this tick |
| `status` | `NodeStatus` | Status after this tick |
| `tick_time` | `float` | `time.monotonic()` timestamp |
| `duration` | `float` | Wall-clock tick duration (seconds) |
| `feedback_message` | `str` | Set via `node.set_feedback_message()` |
| `halt_reason` | `str` | Why the node was halted, when it was |

There is no `failure_reason` on the record — that lives on the node itself
(`node.failure_reason`, set via `node.set_failure_reason()`).

### Custom subscribers

```python
inspector.subscribe(lambda record: my_dashboard.update(record))
```

---

## Logger

Structured per-transition log with pluggable sinks.

```python
from bteng import Logger, LogLevel

logger = Logger.create()
logger.add_console_sink(colored=True)
logger.add_json_file_sink("/tmp/bt_run.jsonl")
logger.set_min_level(LogLevel.DEBUG)

executor.set_logger(logger)   # auto-wired to inspector if both set
```

When both `set_inspector()` and `set_logger()` are called on an executor (in any
order), the logger is automatically subscribed to the inspector — no manual wiring
needed.

### Log levels

| Level | When used |
|-------|-----------|
| `DEBUG` | Every tick event |
| `INFO` | Status transitions (IDLE→RUNNING, RUNNING→SUCCESS, etc.) |
| `WARNING` | Soft errors (port missing but has default, etc.) |
| `ERROR` | Hard errors, unexpected states |

---

## ExecutionTracer

Records per-tick `TraceFrame` snapshots for replay and regression testing.
Opt-in — disabled by default.

```python
from bteng import ExecutionTracer, TreeExecutor

tracer = ExecutionTracer()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_tracer(tracer)
executor.tick_until_result()

tracer.print_summary()         # human-readable summary
tracer.export_json("run.json") # full JSON log
events = tracer.events()       # List[TransitionEvent]
```

With `BehaviorTreeEngine` (legacy):

```python
tracer = ExecutionTracer()
engine = BehaviorTreeEngine(root, tracer=tracer)
engine.run_until_complete()
tracer.save("run.json")
```

---

## ZmqPublisher

Stream inspector events to external dashboards or monitoring tools via ZMQ PUB socket.
Requires `pip install bteng[zmq]`.

```python
from bteng.introspection import Inspector, ZmqPublisher

inspector = Inspector.create()

pub = ZmqPublisher(port=1667)   # default port matches BehaviorTree.CPP convention
pub.attach(inspector)
pub.start()

executor.set_inspector(inspector)
executor.tick_until_result()

pub.stop()
```

### Subscriber (separate process)

```python
import zmq, json

ctx  = zmq.Context()
sock = ctx.socket(zmq.SUB)
sock.connect("tcp://localhost:1667")
sock.setsockopt(zmq.SUBSCRIBE, b"bteng")

while True:
    raw  = sock.recv()
    data = json.loads(raw[len(b"bteng "):])
    print(data)
```

### Message format

```json
{
  "ts":     1234.567,
  "uid":    "a1b2c3d4",
  "name":   "Navigate",
  "type":   "action",
  "status": "SUCCESS",
  "dur_ms": 12.3,
  "reason": ""
}
```

Topic prefix: `bteng ` (with trailing space).
Publisher uses a background thread with a bounded queue (1 000 entries); oldest records
are dropped if the queue fills. Suitable for real-time display — no backpressure.

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `NodeExecutionRecord` | dataclass | `bteng.introspection.inspector` | [inspector.py](../../bteng/introspection/inspector.py) |
| `NodeStats` | dataclass | `bteng.introspection.inspector` | [inspector.py](../../bteng/introspection/inspector.py) |
| `ExplainEntry` | dataclass | `bteng.introspection.inspector` | [inspector.py](../../bteng/introspection/inspector.py) |
| `Inspector` | class | `bteng.introspection.inspector` | [inspector.py](../../bteng/introspection/inspector.py) |
| `LogLevel` | enum | `bteng.introspection.logger` | [logger.py](../../bteng/introspection/logger.py) |
| `LogEntry` | dataclass | `bteng.introspection.logger` | [logger.py](../../bteng/introspection/logger.py) |
| `Logger` | class | `bteng.introspection.logger` | [logger.py](../../bteng/introspection/logger.py) |
| `TraceFrame` | dataclass | `bteng.logging.tracer` | [tracer.py](../../bteng/logging/tracer.py) |
| `ExecutionTracer` | class | `bteng.logging.tracer` | [tracer.py](../../bteng/logging/tracer.py) |
| `ZmqPublisher` | class | `bteng.introspection.zmq_publisher` | [zmq_publisher.py](../../bteng/introspection/zmq_publisher.py) |
