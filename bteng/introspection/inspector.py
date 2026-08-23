"""Runtime introspection: execution history, active path, per-node stats, explainability."""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from bteng.core.node import NodeID, NodeStatus, NodeType


# ── NodeExecutionRecord ───────────────────────────────────────────────────────

@dataclass
class NodeExecutionRecord:
    """Immutable snapshot of one node's state after a single execute_tick() call."""
    uid:            NodeID
    name:           str
    node_type:      NodeType
    status:         NodeStatus
    tick_time:      float         # time.monotonic()
    duration:       float         # seconds
    old_status:       NodeStatus = NodeStatus.IDLE
    feedback_message: str = ""
    halt_reason:      str = ""

    @property
    def failure_reason(self) -> str:
        """Alias for feedback_message. Kept for backward compatibility."""
        return self.feedback_message


# ── ExplainEntry ──────────────────────────────────────────────────────────────

@dataclass
class ExplainEntry:
    """One entry in the explainability log."""
    timestamp:  float
    node_uid:   NodeID
    node_name:  str
    event:      str   # "selected", "succeeded", "failed", "halted", "skipped"
    reason:     str   # e.g., "child 'CheckBattery' returned FAILURE"


# ── NodeStats ─────────────────────────────────────────────────────────────────

@dataclass
class NodeStats:
    """Per-node aggregate statistics across all ticks since last reset()."""
    tick_count:     int   = 0
    success_count:  int   = 0
    failure_count:  int   = 0
    total_duration: float = 0.0   # seconds
    max_duration:   float = 0.0
    min_duration:   float = float("inf")


# ── Inspector ─────────────────────────────────────────────────────────────────

class Inspector:
    """Passive observer that collects data as the executor runs the tree.

    WHAT IT TRACKS
    --------------
    - running_nodes  : set of NodeIDs currently in RUNNING state
    - active_path    : NodeIDs in RUNNING state ordered by first-tick arrival.
                       For sequential trees this is root→leaf; for parallel
                       trees multiple branches appear in tick-arrival order.
    - history        : ring buffer of NodeExecutionRecords (bounded by max_history)
    - stats          : per-node tick/success/failure counts and timing aggregates
    - halt_reasons   : last reason each node was halted for (see on_node_halt)
    - explain_log    : human-readable explanations of why nodes were selected/failed

    Thread safety: a single mutex guards all mutable state.
    """

    EventCallback = Callable[["NodeExecutionRecord"], None]

    @classmethod
    def create(cls) -> "Inspector":
        return cls()

    def __init__(self, max_history: int = 1_000) -> None:
        self._lock          = threading.Lock()
        self._running_nodes: Set[NodeID]               = set()
        self._active_path:   Dict[NodeID, None]        = {}
        self._history:       Deque[NodeExecutionRecord] = deque(maxlen=max_history)
        self._stats:         Dict[NodeID, NodeStats]   = {}
        self._halt_reasons:  Dict[NodeID, str]         = {}
        self._explain_log:   Deque[ExplainEntry]       = deque(maxlen=1000)
        self._subscribers:   Dict[int, "Inspector.EventCallback"] = {}
        self._next_sub_id:   int = 0
        self._max_history:   int = max_history
        self._subscribers_cache: list = []
        self._subscribers_dirty: bool = True

    # ── Called by executor after each node tick ───────────────────────────────

    def on_node_tick(
        self,
        uid:              NodeID,
        name:             str,
        node_type:        NodeType,
        old_status:       NodeStatus,
        new_status:       NodeStatus,
        duration:         float,
        tick_time:        Optional[float] = None,
        feedback_message: str = "",
    ) -> None:
        record = NodeExecutionRecord(
            uid=uid, name=name, node_type=node_type,
            status=new_status, old_status=old_status,
            tick_time=tick_time if tick_time is not None else time.monotonic(),
            duration=duration, feedback_message=feedback_message,
        )
        with self._lock:
            # Update running-node set
            if new_status == NodeStatus.RUNNING:
                self._running_nodes.add(uid)
            else:
                self._running_nodes.discard(uid)

            # Maintain active-path list
            if new_status == NodeStatus.RUNNING and uid not in self._active_path:
                self._active_path[uid] = None
            elif new_status != NodeStatus.RUNNING:
                self._active_path.pop(uid, None)

            # Append to history ring buffer (deque with maxlen handles eviction)
            self._history.append(record)

            # Update per-node stats
            stats = self._stats.setdefault(uid, NodeStats())
            stats.tick_count     += 1
            stats.total_duration += duration
            stats.max_duration    = max(stats.max_duration, duration)
            stats.min_duration    = min(stats.min_duration, duration)
            if new_status == NodeStatus.SUCCESS:
                stats.success_count += 1
            elif new_status == NodeStatus.FAILURE:
                stats.failure_count += 1

            if self._subscribers_dirty:
                self._subscribers_cache = list(self._subscribers.values())
                self._subscribers_dirty = False
            cbs = self._subscribers_cache

        for cb in cbs:
            try:
                cb(record)
            except Exception as exc:
                print(f"[bteng] Inspector subscriber raised: {exc}", file=sys.stderr)

    def on_node_halt(self, uid: NodeID, name: str, reason: str = "") -> None:
        """Called when a node is halted (Timeout fired, reactive re-eval, teardown).

        A halted node is no longer RUNNING, so it must leave running_nodes() and
        active_path() — otherwise a live Groot-style view keeps showing a stale
        active node forever.  ``reason`` is stored on the node's most recent
        execution record (``NodeExecutionRecord.halt_reason``) and in a per-node
        map queryable via halt_reason(uid).

        Deliberately does NOT append to the history ring buffer or the explain
        log: halt() runs on every teardown, and flooding either buffer would
        evict the tick records callers actually inspect.
        """
        with self._lock:
            self._running_nodes.discard(uid)
            self._active_path.pop(uid, None)
            if reason:
                self._halt_reasons[uid] = reason
            # Annotate the latest record for this node so exports/replays carry
            # the reason alongside the tick that was interrupted.
            for record in reversed(self._history):
                if record.uid == uid:
                    record.halt_reason = reason
                    break

    def halt_reason(self, uid: NodeID) -> str:
        """Most recent halt reason recorded for a node ("" if never halted)."""
        with self._lock:
            return self._halt_reasons.get(uid, "")

    def halt_reasons(self) -> Dict[NodeID, str]:
        """Copy of the per-node halt-reason map."""
        with self._lock:
            return dict(self._halt_reasons)

    # ── Query API ─────────────────────────────────────────────────────────────

    def running_nodes(self) -> List[NodeID]:
        """NodeIDs whose status is currently RUNNING."""
        with self._lock:
            return list(self._running_nodes)

    def active_path(self) -> List[NodeID]:
        """NodeIDs currently in RUNNING state, ordered by first-tick arrival.

        For sequential trees this is effectively root→leaf.  For parallel
        trees it reflects tick-arrival order across branches; use
        running_nodes() if order is not important.
        """
        with self._lock:
            return list(self._active_path.keys())

    def execution_history(self, max_entries: int = 0) -> List[NodeExecutionRecord]:
        """Return recent entries from the execution history.

        max_entries=0 returns the full buffer.
        """
        with self._lock:
            if max_entries == 0:
                return list(self._history)
            return list(self._history)[-max_entries:]

    def stats_for(self, uid: NodeID) -> Optional[NodeStats]:
        with self._lock:
            return self._stats.get(uid)

    def all_stats(self) -> Dict[NodeID, NodeStats]:
        with self._lock:
            return dict(self._stats)

    # ── Explainability log ────────────────────────────────────────────────────

    def add_explanation(
        self, uid: NodeID, name: str, event: str, reason: str
    ) -> None:
        """Record a human-readable explanation of a significant execution decision."""
        entry = ExplainEntry(
            timestamp=time.monotonic(), node_uid=uid, node_name=name,
            event=event, reason=reason,
        )
        with self._lock:
            self._explain_log.append(entry)

    def explanations(self) -> List[ExplainEntry]:
        with self._lock:
            return list(self._explain_log)

    # ── Event subscriptions ───────────────────────────────────────────────────

    def subscribe(self, callback: "Inspector.EventCallback") -> int:
        """Register a listener called after each node tick.

        Returns a subscription ID for later removal.  Keep callbacks fast.
        Exceptions raised by callbacks are printed to stderr and suppressed
        so they cannot crash the execution loop.
        """
        with self._lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            self._subscribers[sub_id] = callback
            self._subscribers_dirty = True
            return sub_id

    def unsubscribe(self, subscription_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscription_id, None)
            self._subscribers_dirty = True

    # ── Maintenance ───────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear history, stats, and explain log (subscriptions kept)."""
        with self._lock:
            self._running_nodes.clear()
            self._active_path.clear()
            self._history.clear()
            self._stats.clear()
            self._halt_reasons.clear()
            self._explain_log.clear()

    def set_max_history(self, n: int) -> None:
        with self._lock:
            self._max_history = n
            self._history = deque(self._history, maxlen=n)

    def __repr__(self) -> str:
        return (
            f"Inspector(running={len(self._running_nodes)}, "
            f"history={len(self._history)}, stats={len(self._stats)})"
        )
