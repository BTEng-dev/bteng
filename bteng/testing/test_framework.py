"""Test framework helpers for verifying behavior tree execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from bteng.core.node import NodeID, NodeStatus
from bteng.core.tree import Tree
from bteng.core.executor import TreeExecutor, ExecutorConfig
from bteng.blackboard.blackboard import Blackboard
from bteng.introspection.inspector import Inspector, NodeExecutionRecord


# ── Expected transition ───────────────────────────────────────────────────────

@dataclass
class ExpectedTransition:
    """Declares the expected node name and status at a specific point in execution."""
    node_name:       str
    expected_status: NodeStatus


# ── Simulation override ───────────────────────────────────────────────────────

@dataclass
class SimulationOverride:
    """Replaces a node's tick logic with a callable for the duration of a test."""
    node_name:   str
    override_fn: Callable[[], NodeStatus]
    duration_ms: int = 0  # simulated processing delay (not currently enforced)


# ── TestResult ────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    """Result of a BehaviorTreeTest.run() call."""
    passed:        bool           = False
    error_message: str            = ""
    violations:    List[str]      = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


# ── BehaviorTreeTest ──────────────────────────────────────────────────────────

class BehaviorTreeTest:
    """Declarative test harness for verifying behavior tree execution.

    Usage::

        test = (
            BehaviorTreeTest(tree)
            .set_blackboard("battery_level", 80)
            .expect_final_status(NodeStatus.SUCCESS)
            .set_max_ticks(100)
        )
        result = test.run()
        assert result, result.error_message
    """

    def __init__(
        self,
        tree:    Tree,
        config:  Optional[ExecutorConfig] = None,
    ) -> None:
        self._tree           = tree
        self._exec_config    = config or ExecutorConfig()
        self._overrides:     List[SimulationOverride]  = []
        self._expected_path: List[ExpectedTransition]  = []
        self._expected_final: Optional[NodeStatus]     = None
        self._max_ticks:     int                       = 1000
        self._bb_presets:    Dict[str, Any]            = {}
        self._history:       List[NodeExecutionRecord] = []
        self._final_status:  NodeStatus                = NodeStatus.IDLE

    # ── Setup ─────────────────────────────────────────────────────────────────

    def set_blackboard(self, key: str, value: Any) -> "BehaviorTreeTest":
        self._bb_presets[key] = value
        return self

    def add_sim_override(self, override: SimulationOverride) -> "BehaviorTreeTest":
        self._overrides.append(override)
        return self

    def expect_path(self, transitions: List[ExpectedTransition]) -> "BehaviorTreeTest":
        self._expected_path = transitions
        return self

    def expect_final_status(self, status: NodeStatus) -> "BehaviorTreeTest":
        self._expected_final = status
        return self

    def set_max_ticks(self, n: int) -> "BehaviorTreeTest":
        self._max_ticks = n
        return self

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> TestResult:
        """Execute the tree and validate against expectations."""
        # Pre-populate blackboard
        for key, val in self._bb_presets.items():
            self._tree.blackboard.set(key, val)

        # Apply simulation overrides by monkey-patching node tick()
        self._apply_overrides()

        # Wire inspector
        inspector = Inspector.create()
        executor  = TreeExecutor(self._exec_config)
        executor.set_tree(self._tree)
        executor.set_inspector(inspector)

        # Run
        self._final_status = executor.tick_until_result(max_ticks=self._max_ticks)
        self._history       = inspector.execution_history()

        # Validate
        return self._validate()

    # ── Access results ────────────────────────────────────────────────────────

    def execution_history(self) -> List[NodeExecutionRecord]:
        return list(self._history)

    @property
    def final_status(self) -> NodeStatus:
        return self._final_status

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply_overrides(self) -> None:
        for override in self._overrides:
            node = self._tree.find_node_by_name(override.node_name)
            if node is None:
                continue
            fn = override.override_fn
            # Replace tick() with the override (closure captures original)
            import types
            def make_tick(f):
                def patched_tick(self_node):
                    return f()
                return patched_tick
            node.tick = types.MethodType(make_tick(fn), node)

    def _validate(self) -> TestResult:
        violations: list = []

        # Check final status
        if self._expected_final is not None:
            if self._final_status != self._expected_final:
                violations.append(
                    f"Expected final status {self._expected_final.value}, "
                    f"got {self._final_status.value}"
                )

        # Check expected path (partial match: all listed transitions must appear)
        if self._expected_path:
            # Build a lookup of (node_name, status) from history
            seen = {(r.name, r.status) for r in self._history}
            for t in self._expected_path:
                if (t.node_name, t.expected_status) not in seen:
                    violations.append(
                        f"Expected transition: {t.node_name} → {t.expected_status.value} "
                        f"not found in execution history"
                    )

        passed = len(violations) == 0
        return TestResult(
            passed=passed,
            error_message="; ".join(violations) if violations else "",
            violations=violations,
        )


# ── BlackboardMock ────────────────────────────────────────────────────────────

class BlackboardMock:
    """Convenience wrapper for setting up a Blackboard in tests.

    Usage::

        bb = BlackboardMock()
        bb.set("battery", 80)
        bb.set("goal", (1.0, 2.0))

        # Pass bb.blackboard to tree/engine
        engine = BehaviorTreeEngine(root, blackboard=bb.blackboard)
    """

    def __init__(self) -> None:
        self._bb = Blackboard(scope_name="test")

    @property
    def blackboard(self) -> Blackboard:
        return self._bb

    def set(self, key: str, value: Any) -> "BlackboardMock":
        self._bb.set(key, value)
        return self

    def get(self, key: str, default: Any = None) -> Any:
        return self._bb.get(key, default)

    def reset(self) -> None:
        self._bb.clear()

    def __repr__(self) -> str:
        return f"BlackboardMock({self._bb.snapshot()})"
