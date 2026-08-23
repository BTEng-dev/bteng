"""Core node abstractions for BTEng."""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from bteng.blackboard.blackboard import Blackboard
    from bteng.logging.tracer import ExecutionTracer
    from bteng.introspection.inspector import Inspector

# ── Type alias ────────────────────────────────────────────────────────────────

NodeID = str  # unique node identity; matches C++ NodeID (uint64_t) semantically


# ── Enums ─────────────────────────────────────────────────────────────────────

class NodeStatus(Enum):
    IDLE    = "IDLE"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class NodeType(Enum):
    ACTION            = "action"
    CONDITION         = "condition"
    CONTROL           = "control"
    DECORATOR         = "decorator"
    SEQUENCE          = "sequence"
    FALLBACK          = "fallback"
    PARALLEL          = "parallel"
    REACTIVE_SEQUENCE = "reactive_sequence"
    REACTIVE_FALLBACK = "reactive_fallback"
    SUBTREE           = "subtree"


class PortDirection(Enum):
    INPUT  = "input"
    OUTPUT = "output"
    INOUT  = "inout"   # bidirectional — node both reads and writes


class ExecutionMode(Enum):
    """Execution contract mode declared in NodeContract."""
    SYNCHRONOUS  = "synchronous"
    ASYNCHRONOUS = "asynchronous"


# ── Port declarations ──────────────────────────────────────────────────────────

@dataclass
class PortDefinition:
    name:        str
    direction:   PortDirection
    description: str = ""
    default:     Any = None
    type_hint:   Optional[type] = None  # optional Python type annotation

    def is_input(self) -> bool:
        return self.direction in (PortDirection.INPUT, PortDirection.INOUT)

    def is_output(self) -> bool:
        return self.direction in (PortDirection.OUTPUT, PortDirection.INOUT)


def InputPort(
    name: str,
    description: str = "",
    default: Any = None,
    type_hint: Optional[type] = None,
) -> PortDefinition:
    return PortDefinition(
        name=name, direction=PortDirection.INPUT,
        description=description, default=default, type_hint=type_hint,
    )


def OutputPort(
    name: str,
    description: str = "",
    type_hint: Optional[type] = None,
) -> PortDefinition:
    return PortDefinition(
        name=name, direction=PortDirection.OUTPUT,
        description=description, type_hint=type_hint,
    )


def BidirectionalPort(
    name: str,
    description: str = "",
    type_hint: Optional[type] = None,
) -> PortDefinition:
    """Port that is both read and written by the node (INOUT)."""
    return PortDefinition(
        name=name, direction=PortDirection.INOUT,
        description=description, type_hint=type_hint,
    )


# ── NodeContract ──────────────────────────────────────────────────────────────

@dataclass
class NodeContract:
    """Declarative metadata describing how a node behaves.

    Used by the engine for validation, safe composition, and the
    explainability layer.  Override contract() in your node to populate.
    """
    node_id:              str = ""
    exec_mode:            ExecutionMode = ExecutionMode.SYNCHRONOUS
    expected_max_duration: Optional[float] = None  # seconds; None = unbounded
    possible_failures:    List[str] = field(default_factory=list)
    description:          str = ""


# ── NodeConfig ────────────────────────────────────────────────────────────────

@dataclass
class NodeConfig:
    """Everything a node needs to know at construction time."""
    blackboard:   Optional["Blackboard"] = None
    input_ports:  Dict[str, str] = field(default_factory=dict)   # port → bb_key
    output_ports: Dict[str, str] = field(default_factory=dict)   # port → bb_key
    params:       Dict[str, Any] = field(default_factory=dict)   # port → static value
    node_id:      int = 0          # numeric ID assigned by TreeBuilder/XMLParser


# ── TreeNode ──────────────────────────────────────────────────────────────────

class TreeNode(ABC):
    """Base class for all BT nodes."""

    node_type: NodeType = NodeType.ACTION

    def __init__(self, name: str, config: Optional[NodeConfig] = None) -> None:
        self.name  = name
        self.uid:  NodeID = uuid.uuid4().hex  # 128-bit hex; full entropy avoids collisions in large trees
        self._config:   NodeConfig   = config or NodeConfig()
        self._status:   NodeStatus   = NodeStatus.IDLE
        self._tracer:   Optional["ExecutionTracer"] = None
        self._inspector: Optional["Inspector"]      = None

        # ── Timing metrics (matches C++ Node::tickCount / lastTickDuration) ──
        self._tick_count:         int   = 0
        self._last_tick_duration: float = 0.0   # seconds
        self._last_tick_time:     float = 0.0   # time.monotonic() timestamp
        self._feedback_message:   str   = ""

    # ── Public execution interface ────────────────────────────────────────────

    def execute_tick(self) -> NodeStatus:
        """Primary tick entry point. Records timing, delegates to tick().

        Raises TypeError if tick() did not return one of RUNNING / SUCCESS /
        FAILURE. A missing return path yields None, and control nodes read that
        as "not RUNNING, not FAILURE" — i.e. they walk straight past the node
        and the tree reports SUCCESS. Failing at the offending node beats a
        wrong result three levels up: that silent path is what hid a first-tick
        bug in bteng-ros2's RosActionNode for a whole release.

        NodeStatus.IDLE is rejected for the same reason: it is a *resting*
        state, not a tick result, and every control node misreads it exactly
        like None (a Sequence advances, a Fallback advances, a Parallel never
        completes, a decorator propagates it upward).
        """
        t0 = time.monotonic()
        prev = self._status
        new_status = self.tick()
        if new_status not in (
            NodeStatus.RUNNING, NodeStatus.SUCCESS, NodeStatus.FAILURE
        ):
            raise TypeError(
                f"{type(self).__name__}.tick() returned {new_status!r}, expected NodeStatus"
            )
        self._status = new_status

        # Update per-node metrics
        self._last_tick_duration = time.monotonic() - t0
        self._last_tick_time     = t0
        self._tick_count        += 1

        if self._tracer is not None and prev != new_status:
            self._tracer.log_transition(self, prev, new_status)

        if self._inspector is not None:
            self._inspector.on_node_tick(
                self.uid, self.name, self.node_type,
                prev, new_status, self._last_tick_duration,
                t0,
                self._feedback_message,
            )

        return new_status

    @abstractmethod
    def tick(self) -> NodeStatus:
        """Core node logic. Control nodes call child.execute_tick() from here."""

    def halt(self) -> None:
        """Stop this node. Resets status to IDLE."""
        if self._status == NodeStatus.RUNNING:
            self._on_halt()
            self._notify_halt()
        self._status = NodeStatus.IDLE

    def _notify_halt(self, reason: str = "halted") -> None:
        """Tell the inspector this node stopped running outside of a tick.

        A halt is not a tick, so on_node_tick() never fires for it: without
        this, a node halted by a Timeout or a reactive sibling stays in
        Inspector.running_nodes()/active_path() forever and live views show a
        stale active node.  Call from every halt() override, only when the node
        was actually RUNNING.
        """
        if self._inspector is not None:
            self._inspector.on_node_halt(self.uid, self.name, reason)

    def reset_node(self) -> None:
        """Unconditionally reset this node and all descendants to IDLE.

        Unlike halt(), this does not check current status.  Used to restart
        a whole subtree from scratch (e.g., after replay reset).
        """
        self._on_halt()
        self._on_reset()
        self._status            = NodeStatus.IDLE
        self._feedback_message  = ""
        for child in self.get_children():
            child.reset_node()

    # ── Overridable hooks ────────────────────────────────────────────────────

    def _on_halt(self) -> None:
        """Called when a RUNNING node is halted. Override for cleanup."""

    def _on_reset(self) -> None:
        """Called by reset_node() after _on_halt(). Restore to initial state."""

    # ── One-time lifecycle hooks ──────────────────────────────────────────────

    def setup(self) -> None:
        """One-time resource initialisation. Called once before the first tick.

        Override to open sockets, subscribe to ROS topics, allocate thread
        pools, etc.  Guaranteed to run after all injections (inspector,
        tracer, blackboard) are in place.  Pair with shutdown().
        """

    def shutdown(self) -> None:
        """Release resources acquired in setup(). Called when executor stops."""

    # ── Interface contract ────────────────────────────────────────────────────

    def contract(self) -> NodeContract:
        """Return the declarative metadata for this node type.

        Override in concrete nodes to describe execution mode, expected
        duration, and possible failure reasons.
        """
        return NodeContract(node_id=type(self).__name__)

    # ── Tree traversal ────────────────────────────────────────────────────────

    def get_children(self) -> List["TreeNode"]:
        return []

    def tip(self) -> Optional["TreeNode"]:
        """Return the deepest RUNNING node, or None if nothing is RUNNING.

        Walks the active branch to its leaf.  Useful for answering
        "what is this tree doing right now?" without a full traversal.
        Once the tree has settled on SUCCESS or FAILURE there is no active
        branch left, so tip() is None — it is not a "last node that ran"
        accessor.
        """
        return None

    # ── Blackboard port access ────────────────────────────────────────────────

    def get_input(self, port_name: str, default: Any = None) -> Any:
        """Read a value from an input port.

        Resolution order:
          1. Blackboard key reference ({key} syntax via input_ports mapping)
          2. Static literal value (params dict)
          3. default
        """
        bb_key = self._config.input_ports.get(port_name)
        if bb_key is not None and self._config.blackboard is not None:
            return self._config.blackboard.get(bb_key, default)
        return self._config.params.get(port_name, default)

    def set_output(self, port_name: str, value: Any) -> bool:
        """Write a value to an output port. Returns False if no mapping exists."""
        bb_key = self._config.output_ports.get(port_name)
        if bb_key is not None and self._config.blackboard is not None:
            self._config.blackboard.set(bb_key, value, writer=self.uid)
            return True
        return False

    # ── Failure explainability ────────────────────────────────────────────────

    def set_feedback_message(self, message: str) -> None:
        """Set a human-readable status message visible in logs and inspector.

        Call from tick() for any status — RUNNING, SUCCESS, or FAILURE — to
        explain what the node is currently doing or why it succeeded/failed.
        """
        self._feedback_message = message

    def set_failure_reason(self, reason: str) -> None:
        """Alias for set_feedback_message(). Kept for backward compatibility."""
        self._feedback_message = reason

    @property
    def feedback_message(self) -> str:
        return self._feedback_message

    @property
    def failure_reason(self) -> str:
        """Alias for feedback_message. Kept for backward compatibility."""
        return self._feedback_message

    # ── Port declarations (class-level) ──────────────────────────────────────

    @classmethod
    def provided_ports(cls) -> List[PortDefinition]:
        """Declare what ports this node reads/writes.

        Used by NodeFactory manifests, XML validators, and IDE tooling.
        """
        return []

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def status(self) -> NodeStatus:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._status == NodeStatus.RUNNING

    @property
    def config(self) -> NodeConfig:
        return self._config

    @property
    def blackboard(self) -> Optional["Blackboard"]:
        return self._config.blackboard

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def last_tick_duration(self) -> float:
        """Wall-clock duration of the most recent execute_tick() call (seconds)."""
        return self._last_tick_duration

    @property
    def last_tick_time(self) -> float:
        """time.monotonic() timestamp of the most recent execute_tick() call."""
        return self._last_tick_time

    # ── __repr__ ─────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, status={self._status.value})"


# ── Intermediate base classes ─────────────────────────────────────────────────

class ControlNode(TreeNode):
    """Node with multiple children."""

    node_type = NodeType.CONTROL

    def __init__(
        self,
        name: str,
        children: List[TreeNode],
        config: Optional[NodeConfig] = None,
    ) -> None:
        super().__init__(name, config)
        self._children: List[TreeNode] = list(children)

    def halt(self) -> None:
        if self._status == NodeStatus.RUNNING:
            self._on_halt()
            self._notify_halt()
        self._halt_children()
        self._status = NodeStatus.IDLE

    def _halt_children(self) -> None:
        for child in self._children:
            child.halt()

    def get_children(self) -> List[TreeNode]:
        return self._children

    def tip(self) -> Optional[TreeNode]:
        """Return the deepest RUNNING node in this subtree, else None.

        Only RUNNING nodes are reported; after the branch settles on SUCCESS or
        FAILURE this returns None.  For a Parallel (which can have several
        RUNNING branches at once) only the first RUNNING child's tip is
        reported — use Inspector.running_nodes() to see all of them.
        """
        for child in self._children:
            t = child.tip()
            if t is not None:
                return t
        return self if self._status == NodeStatus.RUNNING else None


class DecoratorNode(TreeNode):
    """Node with exactly one child."""

    node_type = NodeType.DECORATOR

    def __init__(
        self,
        name: str,
        child: TreeNode,
        config: Optional[NodeConfig] = None,
    ) -> None:
        super().__init__(name, config)
        self._child: TreeNode = child

    def halt(self) -> None:
        if self._status == NodeStatus.RUNNING:
            self._on_halt()
            self._notify_halt()
        self._child.halt()
        self._status = NodeStatus.IDLE

    def get_children(self) -> List[TreeNode]:
        """Return the single child, freshly wrapped.

        Built on every call on purpose: TreeBuilder and Tree's runtime
        modifications both reassign ``_child`` after construction, and a cached
        list silently kept the *old* child — hiding the whole real subtree from
        validate_tree, setup()/shutdown(), tracer/inspector injection,
        find_node*, visit(), reset_node() and ascii_tree() while the decorator
        happily ticked the new one.  The list is a copy; mutating it does not
        change the tree.
        """
        return [self._child]

    def tip(self) -> Optional[TreeNode]:
        """Return the deepest RUNNING node in this subtree, else None.

        Only RUNNING nodes are reported; after the branch settles on SUCCESS or
        FAILURE this returns None.
        """
        t = self._child.tip()
        if t is not None:
            return t
        return self if self._status == NodeStatus.RUNNING else None


class LeafNode(TreeNode):
    """Node with no children (actions, conditions)."""

    def get_children(self) -> List[TreeNode]:
        return []

    def tip(self) -> Optional[TreeNode]:
        """Return self while RUNNING, else None (a settled leaf has no tip)."""
        return self if self._status == NodeStatus.RUNNING else None
