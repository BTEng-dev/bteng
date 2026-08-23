<p align="center">
  <img src="docs/images/BTEng.webp" alt="BTEng — Behavior Tree Engine" width="720">
</p>

# BTEng — Behavior Tree Engine for Python

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.2-green)](pyproject.toml)

**BTEng is a Behavior Tree engine for Python with no runtime dependencies.**

`pip install bteng` installs one package and nothing else. Every module the engine loads
at runtime imports only the standard library, so BTEng drops into a ROS node, a PLC bridge
or an embedded controller without touching your dependency tree.

Exactly two modules import anything third-party, and neither is on that path: the ZMQ
event transport (`pip install "bteng[zmq]"`) and the pytest plugin, loaded only by a test
run that asks for it.

Three things it does that are worth knowing about before you choose it:

- **`async def` leaves without an async tick loop.** A `CoroActionNode` awaits your host
  application's coroutines — an HTTP call, a ROS 2 action, an OPC UA read — while the tick
  loop stays ordinary synchronous code. No colouring your whole control loop `async`.
- **Reactive nodes that watch the blackboard.** `ReactiveSequence` and `ReactiveFallback`
  subscribe to the blackboards their subtree can reach and re-check guards on every tick,
  interrupting a running action the moment a precondition stops holding — without
  re-entering the actions ahead of it.
- **Introspection built in, not bolted on.** Every status change is recorded once, inside
  `execute_tick()`, and fanned out to whichever of the Inspector, structured logger,
  execution tracer and optional ZMQ stream you attached. The tracer can save a run and
  replay it frame by frame afterwards.

Use it for robotics, automation, simulation — anything tick-driven that needs
preconditions, fallbacks, retries and recovery paths rather than one large control loop.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [Custom Nodes](#custom-nodes)
- [XML Trees](#xml-trees)
- [Introspection & Logging](#introspection--logging)
- [Execution Tracing & Replay](#execution-tracing--replay)
- [ZMQ Event Streaming](#zmq-event-streaming)
- [Unit Testing](#unit-testing)
- [CLI](#cli)
- [Documentation](#documentation)
- [License](#license)

---

## Features

| Category | Capabilities |
|----------|-------------|
| **Execution** | Tick-based engine, event-loop executor, pause/resume, max-tick budget |
| **Control nodes** | `Sequence`, `Fallback`/`Selector`, `Parallel`, `ReactiveSequence`, `ReactiveFallback` |
| **Decorators** | `Inverter`, `Retry`, `Timeout`, `RateController`, `ForceSuccess`, `ForceFailure` |
| **Leaf nodes** | `ActionNode`, `ConditionNode`, `StatefulActionNode`, `AsyncActionNode` |
| **Blackboard** | Scoped, observable, provenance history, schema validation, change subscriptions |
| **Builder API** | Fluent `TreeBuilder` for programmatic tree construction |
| **XML loader** | Full XML parser with port-default resolution and subtree support |
| **Factory** | `@register_node` decorator, `NodeManifest`, dynamic plugin loading |
| **Introspection** | `Inspector` — per-node stats, active-path tracking, explain log |
| **Logging** | `Logger` — console and JSON-lines sinks, auto-wired to Inspector |
| **Tracing** | `ExecutionTracer` — per-tick frame recording, JSON export, replay |
| **Concurrency** | `ThreadPool` (auto-injected), `CancellationToken` for async actions |
| **Testing** | `MockActionNode`, `MockConditionNode`, `BehaviorTreeTest` |
| **Streaming** | `ZmqPublisher` — streams tick events via ZMQ PUB (optional dep) |
| **CLI** | `bteng run` for command-line tree execution |

---

## Installation

### From PyPI

```bash
pip install bteng
```

### Optional extras

```bash
pip install "bteng[zmq]"   # ZMQ publisher — stream tick events to dashboards
pip install "bteng[dev]"   # Development tools: pytest, build, twine
```

**Requirements:** Python 3.12 or newer.

---

## Quick Start

For new code, start with `TreeBuilder + TreeExecutor`.

```python
from bteng import (
    Blackboard,
    NodeStatus,
    TreeBuilder,
    TreeExecutor,
)

bb = Blackboard.create("demo")
bb.set("battery_level", 87)

tree = (
    TreeBuilder(blackboard=bb)
    .tree_id("Mission")
    .sequence("root")
        .condition("BatteryOK", lambda: bb.get("battery_level", 0) > 20)
        .action("Navigate",     lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
status = executor.tick_until_result(max_ticks=100)
print(status)  # NodeStatus.SUCCESS
```

`BehaviorTreeEngine` is still available for legacy code, but `TreeExecutor` is the
recommended runtime for new projects.

---

## Core Concepts

### Status model

Every node returns a `NodeStatus` each tick:

| Status | Meaning |
|--------|---------|
| `SUCCESS` | Node completed successfully |
| `FAILURE` | Node failed |
| `RUNNING` | Node is active and must be ticked again |
| `IDLE` | Node is inactive or has been halted |

### Control nodes

| Node | Behavior |
|------|----------|
| `Sequence` | Ticks children left-to-right; stops at first `FAILURE` or `RUNNING` |
| `Fallback` / `Selector` | Ticks children left-to-right; stops at first `SUCCESS` or `RUNNING` |
| `Parallel` | Ticks all children simultaneously with configurable success/failure thresholds |
| `ReactiveSequence` | Restarts from child[0] every tick; earlier conditions interrupt later actions |
| `ReactiveFallback` | Restarts from child[0] every tick; higher-priority child can preempt |

### Decorators

| Decorator | Purpose |
|-----------|---------|
| `Inverter` | Swaps `SUCCESS` ↔ `FAILURE`; passes `RUNNING` unchanged |
| `Retry(max_attempts)` | Retries child on `FAILURE` up to N times |
| `Timeout(duration)` | Returns `FAILURE` if child exceeds duration (seconds) |
| `RateController(hz)` | Rate-limits child ticking; returns cached status between ticks |
| `ForceSuccess` | Always returns `SUCCESS` unless child is `RUNNING` |
| `ForceFailure` | Always returns `FAILURE` unless child is `RUNNING` |

### Blackboard

A thread-safe, scoped key-value store shared across the tree:

```python
from bteng import Blackboard

bb = Blackboard.create("robot")
bb.set("pose", (1.0, 2.0, 0.0))
bb.set("stopped", None)       # None is a valid stored value

bb.get("pose")                # (1.0, 2.0, 0.0)
bb.get("stopped")             # None  (the stored None, not a missing-key default)
bb.has("stopped")             # True

# Change subscriptions
sub = bb.subscribe(lambda key, val: print(f"{key} changed to {val}"))
bb.set("pose", (2.0, 3.0, 0.0))  # fires callback
bb.unsubscribe(sub)

# Scoped child blackboard for subtree port isolation
child = bb.create_child_scope("subtree", remapping={"local_goal": "goal"})
child.set("local_goal", "dock")   # transparently writes bb["goal"]
child.get("local_goal")           # transparently reads bb["goal"]
```

---

## Custom Nodes

Define new node types by subclassing and declaring ports:

```python
from bteng import ActionNode, InputPort, OutputPort, NodeStatus, register_node


@register_node()
class DetectObject(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [
            InputPort("camera", default="rgb"),
            OutputPort("object_pose"),
        ]

    def tick(self) -> NodeStatus:
        camera = self.get_input("camera")   # resolves XML attribute or default
        pose   = run_detector(camera)
        if pose is None:
            self.set_failure_reason("detector returned no result")
            return NodeStatus.FAILURE
        self.set_output("object_pose", pose)
        return NodeStatus.SUCCESS
```

Access the full node configuration via `self.config` (`NodeConfig` with blackboard, port mappings, and static params). `self.blackboard` is a convenience shortcut.

**For long-running work** use `StatefulActionNode` (three-phase lifecycle: `on_start` / `on_running` / `on_halted`) or `AsyncActionNode` (runs `execute_async(token)` in a background thread; the executor automatically injects a shared `ThreadPool`).

---

## XML Trees

Load and execute trees defined in XML — useful for decoupling behavior from code:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<BTEng format_version="1.0" main_tree_to_execute="main">
  <Tree ID="main">
    <ReactiveFallback name="root">
      <ReactiveSequence name="navigate_if_safe">
        <Condition ID="PathClear"/>
        <Timeout duration="10.0">
          <Action ID="NavigateTo" goal="{target_goal}"/>
        </Timeout>
      </ReactiveSequence>
      <Action ID="StopRobot"/>
    </ReactiveFallback>
  </Tree>

  <TreeNodesModel>
    <Condition ID="PathClear"/>
    <Action ID="NavigateTo">
      <input_port name="goal"/>
    </Action>
    <Action ID="StopRobot"/>
  </TreeNodesModel>
</BTEng>
```

**Port syntax:**
- `goal="{target_goal}"` — blackboard reference; reads/writes key `target_goal`
- `mode="inspection"` — static literal parameter
- `InputPort(default=...)` is applied when the XML attribute is absent

```python
from bteng import XMLTreeParser, Blackboard

bb   = Blackboard.create("demo")
bb.set("target_goal", "dock_station")

parser = XMLTreeParser()
root   = parser.parse_file("mission.xml", blackboard=bb)
```

See [docs/reference/xml.md](docs/reference/xml.md) for the full specification.

---

## Introspection & Logging

Attach an `Inspector` and `Logger` to any `TreeExecutor` — no changes to node code required:

```python
from bteng import Inspector, Logger, LogLevel, TreeExecutor

inspector = Inspector.create()
logger    = Logger.create()
logger.add_console_sink(colored=True)
logger.add_json_file_sink("/tmp/bt_run.jsonl")

executor = TreeExecutor()
executor.set_tree(tree)
executor.set_inspector(inspector)
executor.set_logger(logger)       # auto-wired; no extra setup needed
executor.tick_until_result()

# Per-node statistics
for uid, stats in inspector.all_stats().items():
    print(f"{uid:40s}  ticks={stats.tick_count:4d}  "
          f"total={stats.total_duration*1000:.1f}ms")
```

---

## Execution Tracing & Replay

Record every tick for offline analysis, regression testing, or replay:

```python
from bteng import ExecutionTracer, TreeExecutor

tracer   = ExecutionTracer()
executor = TreeExecutor()
executor.set_tree(tree)
executor.set_tracer(tracer)
executor.tick_until_result()

# Export full trace
with open("trace.json", "w") as f:
    f.write(tracer.export_json())

# Compact replay format (smaller file)
with open("replay.json", "w") as f:
    f.write(tracer.export_replay())

# Load and inspect a previously recorded run
tracer2 = ExecutionTracer()
tracer2.load_replay(open("replay.json").read())
frame = tracer2.replay_frame(0)
print(frame.tick_index, frame.blackboard_snapshot)
```

---

## ZMQ Event Streaming

Stream tick events to an external dashboard or visualiser with zero coupling between the engine and the consumer:

```bash
pip install "bteng[zmq]"
```

```python
from bteng.introspection import ZmqPublisher

pub = ZmqPublisher(port=1667)   # default port — compatible with BehaviorTree.CPP convention
pub.attach(inspector)
pub.start()

# ... run tree as normal ...

pub.stop()
```

Each published message is a JSON object on ZMQ topic `bteng`:

```json
{
  "ts":     1234.567,
  "uid":    "a1b2c3d4",
  "name":   "NavigateTo",
  "type":   "action",
  "status": "SUCCESS",
  "dur_ms": 12.3,
  "reason": ""
}
```

---

## Unit Testing

`BehaviorTreeTest` and `MockActionNode` / `MockConditionNode` provide a purpose-built test harness:

```python
from bteng import (
    MockActionNode, MockConditionNode, BehaviorTreeTest,
    NodeStatus, SequenceNode, Tree, TreeMetadata,
)

condition = MockConditionNode("IsReady")
condition.set_bool(True)

action = MockActionNode("Navigate")
action.set_ticks_to_complete(3)    # returns RUNNING for 2 ticks, then SUCCESS

root = SequenceNode("root", children=[condition, action])
tree = Tree(TreeMetadata(id="test"), root)

result = (
    BehaviorTreeTest(tree)
    .expect_final_status(NodeStatus.SUCCESS)
    .set_max_ticks(10)
    .run()
)
assert result, result.error_message
```

---

## CLI

Execute and visualize trees from the command line:

```bash
bteng run mission.xml --plugin my_robot_nodes.py --tree main --hz 10 --log run.json -v
```

---

## Documentation

| Document | Contents |
|----------|----------|
| [5-minute Quick Start](docs/start-here/quickstart.md) | Recommended first tree with `TreeBuilder + TreeExecutor` |
| [Which API should I use?](docs/start-here/which-api.md) | Decision table for Python, XML, legacy, and advanced APIs |
| [Beginner Guide](docs/beginner/control-nodes.md) | Control nodes, actions, blackboard, TreeBuilder, and first tests |
| [Advanced Guide](docs/advanced/ports-validation.md) | Ports, reactive internals, plugins, introspection, ZMQ, runtime modification |
| [Architecture](docs/architecture.md) | Component relationships and data-flow diagram |
| [XML specification](docs/reference/xml.md) | Full XML format reference with port syntax |
| [Node system](docs/reference/nodes.md) | Node lifecycle, port model, custom node authoring |
| [All documentation](docs/README.md) | Full index of every page |
| [Changelog](docs/changelog.md) | Release history |
| [Licensing](docs/license/README.md) | Third-party audit and open-source alternatives |

---

## Acknowledgements

BTEng's design is inspired by [BehaviorTree.CPP](https://github.com/BehaviorTree/BehaviorTree.CPP),
a widely used C++ behavior tree library.

## License

BTEng is released under the MIT License. See [LICENSE](LICENSE) for terms.
