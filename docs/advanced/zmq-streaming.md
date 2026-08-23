# ZMQ Streaming

`ZmqPublisher` streams live inspector events to an external process — a dashboard,
monitoring tool, or visualization frontend — over a ZMQ PUB socket.

---

## Prerequisites

ZMQ support is an optional dependency:

```bash
pip install "bteng[zmq]"
```

Without this extra, importing `ZmqPublisher` raises `ImportError`.

---

## How it works

`ZmqPublisher` attaches to an `Inspector` and re-publishes every execution event as a
JSON message on a ZMQ PUB socket. A subscriber on any external process connects to
the socket and receives the stream.

Messages are published on topic `bteng`. Each message is a JSON-encoded dictionary
with at least `event_type`, `node_name`, `node_uid`, `status`, and `timestamp`.

The publisher uses a **bounded queue** and silently drops the oldest messages when
the queue fills. This keeps the publisher non-blocking and suitable for live displays.
It is not suitable as a guaranteed audit log — use `ExecutionTracer` for that.

---

## Minimal publisher setup

```python
from bteng import Inspector, TreeExecutor
from bteng.introspection import ZmqPublisher

inspector = Inspector.create()

publisher = ZmqPublisher(port=1667)
publisher.attach(inspector)
publisher.start()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_inspector(inspector)
executor.tick_until_result()

publisher.stop()
```

`ZmqPublisher(port)` binds to `tcp://*:<port>`. Subscribers connect to
`tcp://localhost:<port>` (or the host's IP for remote subscribers).

---

## Subscriber (any language)

The subscriber connects to the publisher's socket and receives messages:

```python
import zmq, json

ctx = zmq.Context()
sub = ctx.socket(zmq.SUB)
sub.connect("tcp://localhost:1667")
sub.setsockopt_string(zmq.SUBSCRIBE, "bteng")

while True:
    topic, payload = sub.recv_multipart()
    event = json.loads(payload)
    print(event["node_name"], event["status"], event["timestamp"])
```

Because ZMQ PUB/SUB is language-agnostic, you can write the subscriber in any language
with a ZMQ binding (C++, JavaScript, Rust, Go, etc.).

---

## Publisher options

```python
publisher = ZmqPublisher(
    port=1667,           # TCP port to bind to (default: 1667)
    queue_size=1000,     # max buffered events before dropping (default: 1000)
    topic="bteng",       # ZMQ topic prefix (default: "bteng")
)
```

---

## Event format

Each published message is a two-part ZMQ multipart message:

| Part | Content |
|------|---------|
| Part 1 | Topic string (e.g., `b"bteng"`) |
| Part 2 | JSON-encoded event payload |

Example payload:

```json
{
    "event_type": "transition",
    "node_name":  "Navigate",
    "node_uid":   "Navigate-0",
    "status":     "RUNNING",
    "timestamp":  1715000000.123,
    "tick_index": 42
}
```

---

## Combining with Inspector and Logger

`ZmqPublisher` attaches to `Inspector`, not to `Logger`. To log to both a file and ZMQ
simultaneously:

```python
from bteng import Inspector, Logger, LogLevel, TreeExecutor
from bteng.introspection import ZmqPublisher

inspector = Inspector.create()
logger    = Logger.create()
logger.add_json_file_sink("run.log")
logger.set_min_level(LogLevel.INFO)

publisher = ZmqPublisher(port=1667)
publisher.attach(inspector)
publisher.start()

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_inspector(inspector)
executor.set_logger(logger)
executor.tick_until_result()

publisher.stop()
```

---

## Use cases

| Use case | Suitable? |
|----------|-----------|
| Live dashboard displaying active nodes | Yes |
| Recording an audit trail of all transitions | No — use `ExecutionTracer` + file |
| Remote monitoring from a different machine | Yes — subscriber connects to host IP |
| Visualizing tree state in a custom UI | Yes |
| Guaranteed delivery to a message broker | No — messages are dropped on queue overflow |
