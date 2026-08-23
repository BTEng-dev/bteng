# Testing Reference

BTEng provides mock nodes and `BehaviorTreeTest` for focused tree tests.

For a first example, read [Testing your first tree](../beginner/testing-first-tree.md).

---

## API Reference

Most of the engine is type-annotated and every public symbol carries a docstring, so
`help(...)` in a REPL is the fastest reference. The annotations are not verified by a
type checker and BTEng does not ship a `py.typed` marker, so treat them as documentation
rather than a contract. The table below maps each public API symbol to its module.

| Symbol | Kind | Module | Source |
|--------|------|--------|--------|
| `MockActionNode` | class | `bteng.testing.mock_nodes` | [mock_nodes.py](../../bteng/testing/mock_nodes.py) |
| `MockConditionNode` | class | `bteng.testing.mock_nodes` | [mock_nodes.py](../../bteng/testing/mock_nodes.py) |
| `SimulatedActionNode` | class | `bteng.testing.mock_nodes` | [mock_nodes.py](../../bteng/testing/mock_nodes.py) |
| `BehaviorTreeTest` | class | `bteng.testing.test_framework` | [test_framework.py](../../bteng/testing/test_framework.py) |
| `BlackboardMock` | class | `bteng.testing.test_framework` | [test_framework.py](../../bteng/testing/test_framework.py) |
| `TestResult` | dataclass | `bteng.testing.test_framework` | [test_framework.py](../../bteng/testing/test_framework.py) |
