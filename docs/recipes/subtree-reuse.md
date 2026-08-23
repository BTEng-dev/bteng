# Subtree Reuse

Use XML subtrees when the same behavior needs to run multiple times with different
inputs. Each `SubTree` instance gets its own child blackboard scope, so the same
internal key names can map to different parent keys on each invocation.

## When to use this pattern

- A behavior is structurally identical but operates on different data (e.g., "approach
  this object" reused for pickup and dropoff).
- You want to define behavior once in XML and parameterize it at the call site.
- You need namespace isolation so internal keys do not collide.

## Example

```xml
<BTEng main_tree_to_execute="main">
  <Tree ID="main">
    <Sequence name="root">
      <!-- Both use the same "approach" subtree, but with different targets -->
      <SubTree ID="approach" target="{pickup_goal}"/>
      <SubTree ID="approach" target="{dropoff_goal}"/>
    </Sequence>
  </Tree>

  <Tree ID="approach">
    <Sequence name="approach_root">
      <Condition ID="TargetValid" target="{target}"/>
      <Action    ID="Navigate"    target="{target}"/>
    </Sequence>
  </Tree>
</BTEng>
```

## How scoping works

Each `SubTree` element creates a child blackboard scope. The XML attributes on
`<SubTree>` define the key remapping:

| Outer key | Inner key |
|-----------|-----------|
| `pickup_goal` | `target` (first instance) |
| `dropoff_goal` | `target` (second instance) |

Inside the `approach` subtree, all nodes read from `target`. Outside, the main tree
only sees `pickup_goal` and `dropoff_goal`. The subtree's internal keys cannot
accidentally overwrite the parent blackboard.

## Python equivalent

The same remapping can be created manually in Python using child scopes:

```python
from bteng import Blackboard

parent = Blackboard.create("mission")
parent.set("pickup_goal",  (1.0, 0.0))
parent.set("dropoff_goal", (5.0, 0.0))

scope1 = parent.create_child_scope("approach_1", remapping={"target": "pickup_goal"})
scope2 = parent.create_child_scope("approach_2", remapping={"target": "dropoff_goal"})

print(scope1.get("target"))   # (1.0, 0.0)
print(scope2.get("target"))   # (5.0, 0.0)
```

For the full scoping API, see [Blackboard scoping](../advanced/blackboard-scoping.md).
