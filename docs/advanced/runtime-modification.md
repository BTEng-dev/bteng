# Runtime Tree Modification

Runtime modification lets you change the structure of a behavior tree — adding,
removing, or replacing nodes — while the executor is running, without stopping or
restarting it.

---

## How it works

Modifications are **queued**, not applied immediately. The executor applies all pending
modifications atomically at the start of the next tick, before any node is ticked.
This guarantees that the tree is never modified mid-tick, which would risk corrupting
execution state.

```
tick N:      user calls tree.queue_modification(...)
             modification added to pending queue

tick N+1:    executor applies all pending modifications (atomic)
             tick proceeds with the new tree structure
```

---

## Queuing a modification

```python
from bteng import ModificationType, TreeModification

tree.queue_modification(TreeModification(
    type=ModificationType.REPLACE_NODE,
    target_uid=old_node.uid,
    new_node=new_node,
))
```

`TreeModification` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `type` | `ModificationType` | The kind of modification to apply |
| `target_uid` | `str` | UID of the node to target |
| `new_node` | `TreeNode \| None` | Replacement or addition node (where applicable) |

---

## Modification types

| `ModificationType` | Effect |
|--------------------|--------|
| `REPLACE_NODE` | Replace `target_uid` with `new_node` in the tree |
| `ADD_CHILD` | Add `new_node` as a child of `target_uid` |
| `REMOVE_NODE` | Remove `target_uid` from the tree |

---

## Example — hot-swap a subtree

```python
import time
from bteng import (
    ActionNode, ModificationType, NodeStatus, SequenceNode,
    Tree, TreeExecutor, TreeMetadata, TreeModification,
)

class PrimaryTask(ActionNode):
    def tick(self):
        return NodeStatus.RUNNING   # simulates a long-running task

class BackupTask(ActionNode):
    def tick(self):
        return NodeStatus.SUCCESS

primary = PrimaryTask("primary")
root    = SequenceNode("root", children=[primary])
tree    = Tree(TreeMetadata(id="hotswap"), root)

executor = TreeExecutor()
executor.set_tree(tree)

# Run a tick — primary is RUNNING
print(executor.tick_once())   # NodeStatus.RUNNING

# Queue a replacement while primary is still running
backup = BackupTask("backup")
tree.queue_modification(TreeModification(
    type=ModificationType.REPLACE_NODE,
    target_uid=primary.uid,
    new_node=backup,
))

# Next tick applies the modification first, then ticks with backup
print(executor.tick_once())   # NodeStatus.SUCCESS
```

Expected output:

```text
NodeStatus.RUNNING
NodeStatus.SUCCESS
```

---

## Queuing multiple modifications

Multiple modifications can be queued before the next tick. They are applied in
queue order:

```python
tree.queue_modification(TreeModification(
    type=ModificationType.REMOVE_NODE,
    target_uid=sensor_check.uid,
))
tree.queue_modification(TreeModification(
    type=ModificationType.ADD_CHILD,
    target_uid=root.uid,
    new_node=new_sensor_check,
))
```

---

## When to use runtime modification

Runtime modification is a power feature. In most cases, you do not need it.

| Use it for | Avoid it for |
|------------|--------------|
| Behavior that genuinely must change while the system is running | Static mission logic that can be configured at startup |
| Adaptive systems where the mission plan is updated mid-run | Trees where a `Fallback` or `ReactiveSequenceNode` can express the same logic |
| Tooling and editors that inject behavior interactively | Any case where restarting the executor is acceptable |

`ReactiveSequenceNode`, blackboard state, and `Fallback` can often express dynamic
behavior without restructuring the tree at runtime. Prefer those when possible.

---

## Thread safety

`queue_modification()` is safe to call from any thread. The pending queue is protected
by a lock. The executor drains the queue at the start of each tick under the same lock,
ensuring no partial application.

Do not call `queue_modification()` from inside a node's `tick()` method during the
same tick — the modification will be applied at the start of the *following* tick, not
immediately.
