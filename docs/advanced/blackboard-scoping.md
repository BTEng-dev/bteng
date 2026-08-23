# Blackboard Scoping

Blackboard child scopes let a subtree work in an isolated namespace while still
communicating with the parent tree through explicit key remapping. This is the
mechanism that prevents a subtree's internal keys from polluting the parent blackboard.

---

## Why scoping matters

When you reuse a subtree in multiple places, each instance needs its own "view" of the
blackboard. Without scoping, all instances share the same flat keyspace:

```
parent bb: {"goal": (1,0), "result": "done"}
subtree_1 writes "result" → overwrites subtree_2's "result"
```

With a child scope, each subtree instance maps its local key names to different parent
keys:

```
parent bb: {"pickup_result": "done", "dropoff_result": "done"}
subtree_1 child scope: {"result" → "pickup_result"}
subtree_2 child scope: {"result" → "dropoff_result"}
```

---

## Creating a child scope

```python
from bteng import Blackboard

parent = Blackboard.create("robot")
parent.set("goal", "dock")

child = parent.create_child_scope("approach", remapping={"target": "goal"})
```

The `remapping` dict maps **child key names** to **parent key names**.

### Reading through a scope

```python
child.get("target")    # resolves "target" → parent["goal"] → "dock"
```

### Writing through a scope

```python
child.set("target", "charger")   # forwards to parent["goal"]
print(parent.get("goal"))         # "charger"
```

### Fall-through for unmapped keys

Keys not in the remapping fall through from child to parent:

```python
parent.set("speed", 0.5)
child.get("speed")    # not in remapping → falls through to parent["speed"] → 0.5
```

Non-remapped writes remain **local** to the child scope and do not propagate to the
parent:

```python
child.set("internal_flag", True)
parent.has("internal_flag")   # False — stayed in child scope
```

---

## Scoping rules summary

| Operation | Remapped key | Unmapped key |
|-----------|--------------|--------------|
| Read | Resolves to parent key | Falls through to parent |
| Write | Forwarded to parent key | Stays local in child scope |

---

## Usage in SubTree (XML)

When the XML parser encounters a `<SubTree>` element, it automatically creates a child
scope for that subtree. Port attributes on the `<SubTree>` element define the remapping:

```xml
<BTEng main_tree_to_execute="main">
  <Tree ID="main">
    <Sequence name="root">
      <!-- Each SubTree gets its own child scope -->
      <SubTree ID="approach" target="{pickup_goal}"/>
      <SubTree ID="approach" target="{dropoff_goal}"/>
    </Sequence>
  </Tree>

  <Tree ID="approach">
    <Sequence name="approach_root">
      <!-- "target" is a local name; mapped to different parent keys each time -->
      <Condition ID="TargetValid" target="{target}"/>
      <Action    ID="Navigate"    target="{target}"/>
    </Sequence>
  </Tree>
</BTEng>
```

The first `SubTree` creates a child scope with `{"target": "pickup_goal"}`. The second
creates one with `{"target": "dropoff_goal"}`. Both run the same `approach` behavior
but operate on different parent keys.

---

## Nested scopes

Child scopes can be nested. Each level's remapping applies independently:

```
parent_bb  [scope: "robot"]
    └── child_bb  [scope: "mission", remapping: {"goal" → "mission_goal"}]
            └── grandchild_bb  [scope: "subtask", remapping: {"target" → "goal"}]
```

A write to `grandchild.set("target", x)` propagates:
- Grandchild "target" → child "goal" (via grandchild remapping)
- Child "goal" → parent "mission_goal" (via child remapping)

---

## Practical example

```python
from bteng import Blackboard

parent = Blackboard.create("mission")
parent.set("pickup_goal",  (1.0, 0.0))
parent.set("dropoff_goal", (5.0, 0.0))

# First subtree instance
scope1 = parent.create_child_scope("approach_1", remapping={"target": "pickup_goal"})
print(scope1.get("target"))    # (1.0, 0.0)

# Second subtree instance
scope2 = parent.create_child_scope("approach_2", remapping={"target": "dropoff_goal"})
print(scope2.get("target"))    # (5.0, 0.0)

# Write through scope1 updates pickup_goal, not dropoff_goal
scope1.set("target", (1.5, 0.5))
print(parent.get("pickup_goal"))   # (1.5, 0.5)
print(parent.get("dropoff_goal"))  # (5.0, 0.0) — unchanged
```
