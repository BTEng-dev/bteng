# XML Tree from Python Nodes

Define node behavior in Python and reference it from an XML tree. This pattern is
useful when behavior should be editable outside Python — by non-programmers, at
runtime, or through an external editor — while keeping the node implementation in code.

## When to use this pattern

- Behavior needs to be authored or modified without redeploying Python code.
- You want a configuration-driven system where trees are data, not code.
- You are integrating with tools that produce BehaviorTree.CPP-compatible XML.

## Step 1 — define and register nodes

```python
from bteng import ActionNode, ConditionNode, NodeStatus, register_node

@register_node()
class IsReady(ConditionNode):
    def tick(self):
        return NodeStatus.SUCCESS if self.blackboard.get("ready", False) else NodeStatus.FAILURE

@register_node()
class DoWork(ActionNode):
    def tick(self):
        return NodeStatus.SUCCESS
```

## Step 2 — write the XML tree

```xml
<BTEng main_tree_to_execute="main">
  <Tree ID="main">
    <Sequence name="root">
      <Condition ID="IsReady"/>
      <Action    ID="DoWork"/>
    </Sequence>
  </Tree>
</BTEng>
```

The `ID` attribute on each node must match the class name (or the custom name passed
to `@register_node(name="...")`).

## Step 3 — load and run

```python
from bteng import BehaviorTreeEngine, Blackboard

bb = Blackboard.create("xml_recipe")
bb.set("ready", True)

engine = BehaviorTreeEngine.from_xml("tree.xml", blackboard=bb)
print(engine.run_until_complete())
```

Expected output:

```text
NodeStatus.SUCCESS
```

## Port declarations in XML

If your nodes declare ports, map them in the XML attributes using curly-brace syntax
for blackboard keys and plain text for static literals:

```python
@register_node()
class Navigate(ActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("goal"), OutputPort("result")]

    def tick(self):
        goal = self.get_input("goal")
        self.set_output("result", f"arrived:{goal}")
        return NodeStatus.SUCCESS
```

```xml
<Action ID="Navigate" goal="{current_goal}" result="{nav_result}"/>
```

## Notes

- Nodes must be registered (via `@register_node()` or `load_plugin_file()`) **before**
  `BehaviorTreeEngine.from_xml()` is called. Unregistered IDs cause a `KeyError` at
  load time.
- For pure Python projects, use `TreeBuilder` instead — XML adds a parsing step and an
  external file dependency without benefit.
- See [Plugins and NodeFactory](../advanced/plugins-nodefactory.md) for distributing
  registered nodes as a package.
