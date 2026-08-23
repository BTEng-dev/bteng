# BTEng XML Specification

## Root Element

```xml
<BTEng format_version="1.0" main_tree_to_execute="main_tree">
  ...
</BTEng>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `format_version` | no | Schema version (currently `1.0`) |
| `main_tree_to_execute` | no | ID of tree to run (default: first `<Tree>`) |

---

## Tree Definition

```xml
<Tree ID="my_tree">
  <!-- exactly one root node -->
  <Sequence name="root">
    ...
  </Sequence>
</Tree>
```

Each `<Tree>` has exactly one root child. Multiple `<Tree>` elements allowed (subtrees).

---

## Built-in Node Tags

### Control Nodes

```xml
<Sequence name="optional_label">
  ...children...
</Sequence>

<Fallback name="...">   <!-- also: Selector -->
  ...
</Fallback>

<Parallel success_threshold="2" failure_threshold="1">
  ...
</Parallel>

<ReactiveSequence>
  ...
</ReactiveSequence>

<ReactiveFallback>
  ...
</ReactiveFallback>
```

`Parallel` attributes:
- `success_threshold`: integer, any value `<= 0` means all children (default `-1`)
- `failure_threshold`: integer, default `1`

Both are read as **input ports** on every tick, so either may be a literal or a
`{blackboard_ref}`. Only `success_threshold` appears in `provided_ports()` — it
needs a declared default so a bare `<Parallel>` validates — while
`failure_threshold` is read through the same mechanism without being declared:

```xml
<Parallel success_threshold="{quorum}" failure_threshold="1">
  ...
</Parallel>
```

### Decorators

```xml
<Inverter>
  <Action ID="..."/>
</Inverter>

<Retry num_attempts="5">   <!-- max_attempts is accepted too -->
  <Action ID="..."/>
</Retry>

<Timeout duration="2.5">   <!-- seconds -->
  <Action ID="..."/>
</Timeout>

<RateController hz="10.0">
  <Action ID="..."/>
</RateController>

<ForceSuccess>
  <Action ID="..."/>
</ForceSuccess>

<ForceFailure>
  <Condition ID="..."/>
</ForceFailure>
```

Decorator parameters are **input ports**, re-read on every tick, so a tree can
drive them from the blackboard:

```xml
<Retry num_attempts="{max_tries}">
  <Action ID="Navigate"/>
</Retry>

<Timeout duration="{deadline}">
  <Action ID="Navigate"/>
</Timeout>
```

| Decorator | Port | Also accepts | Default |
|---|---|---|---|
| `Retry` | `num_attempts` | `max_attempts` | constructor argument |
| `Timeout` | `duration` (seconds) | — | constructor argument |
| `RateController` | `hz` | — | constructor argument |

Resolution order is blackboard mapping, then a literal XML attribute, then the
constructor argument, so nothing that worked before changes. A value that is
non-numeric or non-positive keeps the previous behaviour and reports it in
`feedback_message` rather than raising mid-tick.

`<Timeout msec="{k}">` is rejected outright: `msec` is not a port, and binding it
silently would have produced a timeout of milliseconds-read-as-seconds.

---

## Leaf Nodes

```xml
<!-- Look up by ID in NodeFactory registry -->
<Action ID="MyAction" param1="value" port1="{bb_key}"/>
<Condition ID="MyCondition"/>
```

The tags `Action` / `Condition` are semantic — both look up by `ID` in the
`NodeFactory`. Unrecognised tags are also looked up by name directly in the factory.

---

## Port Remapping

```xml
<Action ID="Move" target="{goal_position}" speed="1.5"/>
```

| Syntax | Meaning |
|--------|---------|
| `attr="{key}"` | Blackboard read/write using key `key` |
| `attr="value"` | Static string parameter |

Port direction (input vs output) is determined from `TreeNodesModel` declarations, or
defaults to input if absent.

### Input port defaults

`InputPort(name, default=...)` declared in `provided_ports()` is honoured at runtime.
When an attribute is **absent from the XML element**, the declared default is used.
When the attribute is present, the XML value overrides the default.

```python
@register_node()
class MoveJ(StatefulActionNode):
    @classmethod
    def provided_ports(cls):
        return [InputPort("speed", default="1.05")]
```

```xml
<!-- speed = "1.05" (default applied) -->
<Action ID="MoveJ"/>

<!-- speed = "0.5" (XML overrides default) -->
<Action ID="MoveJ" speed="0.5"/>

<!-- speed read from blackboard key "joint_speed" -->
<Action ID="MoveJ" speed="{joint_speed}"/>
```

---

## SubTree

```xml
<SubTree ID="pick" target="{current_goal}" drop_zone="{bin}"/>
```

- References a `<Tree ID="pick">` defined in the same file
- `target="{current_goal}"` creates a child Blackboard where local key `target`
  maps to parent key `current_goal`
- Static values (`key="fixed"`) are set directly in the child Blackboard
- Keys not listed in the remapping fall through to the parent Blackboard

---

## Generic Node Syntax

```xml
<Node type="MyCustomNode" param="value" input="{bb_key}"/>
```

Use when you want to avoid tag-name collisions or prefer explicit typing. `type` must
be registered in NodeFactory. No parser changes needed for new types.

---

## TreeNodesModel (optional)

Declares port metadata — used to determine whether a `{key}` attribute is an input or
output port. Optional; parser defaults to input if absent.

```xml
<TreeNodesModel>
  <Action ID="Move">
    <input_port name="target" default="home"/>
    <output_port name="arrival_status"/>
  </Action>
  <Condition ID="IsReady"/>
</TreeNodesModel>
```

The `default` attribute on `<input_port>` is informational in the model XML; runtime
defaults come from `InputPort(default=...)` declared in `provided_ports()`.

---

## Full Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<BTEng format_version="1.0" main_tree_to_execute="main">

  <Tree ID="main">
    <ReactiveFallback name="root">
      <ReactiveSequence name="navigate">
        <Condition ID="IsPathClear"/>
        <Timeout duration="10.0">
          <Action ID="Navigate" target="{goal}"/>
        </Timeout>
      </ReactiveSequence>
      <SubTree ID="recovery" goal="{goal}"/>
    </ReactiveFallback>
  </Tree>

  <Tree ID="recovery">
    <Sequence name="recover_seq">
      <Action ID="BackUp"/>
      <Action ID="Replan" target="{goal}"/>
    </Sequence>
  </Tree>

  <TreeNodesModel>
    <Condition ID="IsPathClear"/>
    <Action ID="Navigate">
      <input_port name="target"/>
    </Action>
    <Action ID="BackUp"/>
    <Action ID="Replan">
      <input_port name="target"/>
    </Action>
  </TreeNodesModel>

</BTEng>
```

---

## Extensibility

New node types require **no parser changes**:

1. Define a class and register it:
   ```python
   @register_node()
   class MySpecialNode(ActionNode):
       @classmethod
       def provided_ports(cls):
           return [InputPort("param", default="default_val")]
   ```

2. Use in XML:
   ```xml
   <Action ID="MySpecialNode" param="{key}"/>
   <!-- or explicit generic form -->
   <Node type="MySpecialNode" param="static_value"/>
   ```

The parser resolves `ID` / `type` at tree-build time via NodeFactory and seeds port
defaults from the node manifest before processing XML attributes.

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `XMLTreeParser` | class | `bteng.xml_parser.parser` | [parser.py](../../bteng/xml_parser/parser.py) |
