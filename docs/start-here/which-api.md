# Which API Should I Use?

## New user? Use this.

```python
from bteng import TreeBuilder, TreeExecutor
```

That is the answer for almost all new code. `TreeBuilder` constructs the tree in
Python; `TreeExecutor` runs it. Start there and add other tools only when you hit a
specific need.

---

## Decision table

| Goal | Recommended API |
|------|-----------------|
| Build a tree in Python | `TreeBuilder` |
| Run a tree | `TreeExecutor` |
| Define reusable application nodes | `ActionNode`, `ConditionNode`, `@register_node` |
| Share data between nodes | `Blackboard` |
| Multi-tick actions | `StatefulActionNode` |
| Non-blocking background work | `AsyncActionNode` |
| Debug or monitor execution | `Inspector`, `Logger`, `ExecutionTracer` |
| Load behavior from files (optional) | XML + `XMLTreeParser` or `BehaviorTreeEngine.from_xml` |
| Integrate external node packages (optional) | `NodeFactory`, plugin loading |
| Maintain old code | `BehaviorTreeEngine` |

---

## What beginners do not need immediately

The following features are available, but they are not part of the first learning path.
Skip them until you need them:

| Feature | When it becomes relevant |
|---------|--------------------------|
| XML loading | When behavior should be editable outside Python |
| `NodeFactory` and plugins | When distributing reusable node packages |
| ZMQ streaming | When building a live monitoring dashboard |
| Runtime tree modification | When you need to hot-swap subtrees while the executor is running |
| `BehaviorTreeEngine` | Only when maintaining existing code that uses it |

---

## Legacy compatibility

`BehaviorTreeEngine` is the original BTEng API. It is still supported and will not be
removed, but it should not be the first API new users learn.

Prefer `TreeExecutor` for new projects because it:

- Validates port declarations before the first tick
- Wires introspection tools (`Inspector`, `Logger`, `ExecutionTracer`)
- Supports event-loop execution with pause/resume
- Injects `AsyncActionNode` thread pools automatically

If you are working with existing code that uses `BehaviorTreeEngine`, see
[Executor & Engine](../reference/executor.md#behaviortreeengine-legacy) for the
backward-compatible API.

---

## The full picture

```
New user path
─────────────────────────────────────────────────────────────────
TreeBuilder ──► TreeExecutor ──► Inspector / Logger (optional)
                    │
                    └─► Blackboard (shared state)

Advanced / optional
─────────────────────────────────────────────────────────────────
XMLTreeParser ──► BehaviorTreeEngine.from_xml
NodeFactory   ──► plugin loading
ZmqPublisher  ──► external dashboards
EventBus      ──► application-level event handling
```

Start with the left column. Move right as requirements grow.
