"""BTEng — Modular Behavior Tree execution engine."""

# ── Core node types ────────────────────────────────────────────────────────────
from bteng.core.node import (
    NodeStatus,
    NodeType,
    NodeConfig,
    NodeContract,
    ExecutionMode,
    PortDirection,
    PortDefinition,
    InputPort,
    OutputPort,
    BidirectionalPort,
    TreeNode,
    ControlNode,
    DecoratorNode,
    LeafNode,
)

# ── Legacy engine (backward compatible) ───────────────────────────────────────
from bteng.core.engine import BehaviorTreeEngine

# ── New Tree / Executor / Builder ─────────────────────────────────────────────
from bteng.core.tree import (
    Tree,
    TreeMetadata,
    TreeRegistry,
    TreeModification,
    ModificationType,
)
from bteng.core.executor import TreeExecutor, ExecutorConfig, EventBus, BehaviorEvent
from bteng.core.tree_builder import TreeBuilder

# ── Blackboard ────────────────────────────────────────────────────────────────
from bteng.blackboard.blackboard import (
    Blackboard,
    BlackboardEntry,
    BlackboardHistoryRecord,
    PortSchema,
)

# ── Factory ───────────────────────────────────────────────────────────────────
from bteng.factory.factory import (
    NodeFactory,
    NodeManifest,
    PluginLoadError,
    register_node,
)

# ── Control nodes ─────────────────────────────────────────────────────────────
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.control.fallback import FallbackNode
from bteng.nodes.control.parallel import ParallelNode, ParallelPolicy
from bteng.nodes.control.reactive_sequence import ReactiveSequenceNode
from bteng.nodes.control.reactive_fallback import ReactiveFallbackNode

# ── Decorators ────────────────────────────────────────────────────────────────
from bteng.nodes.decorators.inverter import Inverter
from bteng.nodes.decorators.retry import Retry
from bteng.nodes.decorators.timeout import Timeout
from bteng.nodes.decorators.rate_controller import RateController
from bteng.nodes.decorators.force_result import ForceSuccess, ForceFailure

# ── Leaf nodes ────────────────────────────────────────────────────────────────
from bteng.nodes.leaf.action import ActionNode, FunctionAction, action
from bteng.nodes.leaf.condition import ConditionNode, FunctionCondition, condition
from bteng.nodes.leaf.stateful_action import StatefulActionNode
from bteng.nodes.leaf.async_action import AsyncActionNode
from bteng.nodes.leaf.coro_action import CoroActionNode, FunctionCoroAction, coro_action
from bteng.nodes.leaf.builtins import (
    AlwaysSuccess, AlwaysFailure, AlwaysRunning,
    SetBlackboard, CheckBlackboard,
)
from bteng.nodes.subtree import SubTree

# ── Concurrency ───────────────────────────────────────────────────────────────
from bteng.concurrency.cancellation_token import CancellationToken
from bteng.concurrency.thread_pool import ThreadPool
from bteng.concurrency.asyncio_bridge import (
    AsyncioBridge, get_default_bridge, set_default_bridge, shutdown_default_bridge,
)
from bteng.concurrency.clock import Clock, WallClock

# ── Introspection ─────────────────────────────────────────────────────────────
from bteng.introspection.inspector import Inspector, NodeExecutionRecord, NodeStats, ExplainEntry
from bteng.introspection.logger import Logger, LogEntry, LogLevel
from bteng.introspection.zmq_publisher import ZmqPublisher
from bteng.introspection.renderer import ascii_tree, print_tree

# ── Tracing ───────────────────────────────────────────────────────────────────
from bteng.logging.tracer import ExecutionTracer, TraceFrame, TransitionEvent

# ── Validation ───────────────────────────────────────────────────────────────
from bteng.core.validation import PortValidationError, TreeValidationError

# ── XML / Plugin ──────────────────────────────────────────────────────────────
from bteng.xml_parser.parser import XMLTreeParser
from bteng.plugins.loader import load_plugin_file, load_plugin_module

# ── Testing utilities ─────────────────────────────────────────────────────────
from bteng.testing.mock_nodes import (
    MockActionNode, MockConditionNode, SimConfig, SimulatedActionNode,
)
from bteng.testing.test_framework import BehaviorTreeTest, BlackboardMock, TestResult

from importlib.metadata import version, PackageNotFoundError
try:
    __version__ = version("bteng")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    # ── Node primitives ──────────────────────────────────────────────────────
    "NodeStatus", "NodeType", "NodeConfig", "NodeContract",
    "ExecutionMode", "PortDirection", "PortDefinition",
    "InputPort", "OutputPort", "BidirectionalPort",
    # ── Base classes ─────────────────────────────────────────────────────────
    "TreeNode", "ControlNode", "DecoratorNode", "LeafNode",
    # ── Engine (legacy) ──────────────────────────────────────────────────────
    "BehaviorTreeEngine",
    # ── Tree / Executor / Builder ────────────────────────────────────────────
    "Tree", "TreeMetadata", "TreeRegistry", "TreeModification", "ModificationType",
    "TreeExecutor", "ExecutorConfig", "EventBus", "BehaviorEvent",
    "TreeBuilder",
    # ── Blackboard ───────────────────────────────────────────────────────────
    "Blackboard", "BlackboardEntry", "BlackboardHistoryRecord", "PortSchema",
    # ── Validation ───────────────────────────────────────────────────────────
    "PortValidationError", "TreeValidationError",
    # ── Factory ──────────────────────────────────────────────────────────────
    "NodeFactory", "NodeManifest", "register_node", "PluginLoadError",
    # ── Control nodes ────────────────────────────────────────────────────────
    "SequenceNode", "FallbackNode", "ParallelNode", "ParallelPolicy",
    "ReactiveSequenceNode", "ReactiveFallbackNode",
    # ── Decorators ───────────────────────────────────────────────────────────
    "Inverter", "Retry", "Timeout", "RateController",
    "ForceSuccess", "ForceFailure",
    # ── Leaf nodes ───────────────────────────────────────────────────────────
    "ActionNode", "FunctionAction", "action",
    "ConditionNode", "FunctionCondition", "condition",
    "StatefulActionNode", "AsyncActionNode",
    "CoroActionNode", "FunctionCoroAction", "coro_action",
    "AlwaysSuccess", "AlwaysFailure", "AlwaysRunning",
    "SetBlackboard", "CheckBlackboard",
    "SubTree",
    # ── Concurrency ──────────────────────────────────────────────────────────
    "CancellationToken", "ThreadPool",
    "AsyncioBridge", "get_default_bridge", "set_default_bridge", "shutdown_default_bridge",
    "Clock", "WallClock",
    # ── Introspection ────────────────────────────────────────────────────────
    "Inspector", "NodeExecutionRecord", "NodeStats", "ExplainEntry",
    "Logger", "LogEntry", "LogLevel",
    "ZmqPublisher",
    "ascii_tree", "print_tree",
    # ── Tracing ──────────────────────────────────────────────────────────────
    "ExecutionTracer", "TraceFrame", "TransitionEvent",
    # ── Utilities ────────────────────────────────────────────────────────────
    "XMLTreeParser",
    "load_plugin_file", "load_plugin_module",
    # ── Testing ──────────────────────────────────────────────────────────────
    "MockActionNode", "MockConditionNode", "SimConfig", "SimulatedActionNode",
    "BehaviorTreeTest", "BlackboardMock", "TestResult",
]
