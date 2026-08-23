# Blackboard Reference

The blackboard is the shared data store used by trees and nodes.

For a tutorial, read [Blackboard basics](../beginner/blackboard-basics.md). For advanced
scope behavior, read [Blackboard scoping](../advanced/blackboard-scoping.md).

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `Blackboard` | class | `bteng.blackboard.blackboard` | [blackboard.py](../../bteng/blackboard/blackboard.py) |
| `BlackboardEntry` | dataclass | `bteng.blackboard.blackboard` | [blackboard.py](../../bteng/blackboard/blackboard.py) |
| `BlackboardHistoryRecord` | dataclass | `bteng.blackboard.blackboard` | [blackboard.py](../../bteng/blackboard/blackboard.py) |
| `PortSchema` | dataclass | `bteng.blackboard.blackboard` | [blackboard.py](../../bteng/blackboard/blackboard.py) |
