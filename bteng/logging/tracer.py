"""Per-tick execution recorder for replay and regression testing."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from bteng.core.node import NodeStatus, TreeNode
    from bteng.introspection.inspector import NodeExecutionRecord


# ── Legacy TransitionEvent (kept for backward compatibility) ──────────────────

@dataclass
class TransitionEvent:
    timestamp:   float
    node_name:   str
    node_uid:    str
    node_type:   str
    prev_status: str
    new_status:  str
    duration_ms: float


# ── TraceFrame ────────────────────────────────────────────────────────────────

@dataclass
class TraceFrame:
    """Snapshot of one complete tick cycle.

    Produced by Tracer.begin_frame() / end_frame().  The blackboard_snapshot
    is optional (populated only when end_frame() is called with a snapshot dict).
    """
    tick_index:          int
    timestamp:           float
    node_records:        List[Any]          # List[NodeExecutionRecord]
    blackboard_snapshot: Dict[str, str]     # key → str(value), optional


# ── ExecutionTracer ───────────────────────────────────────────────────────────

class ExecutionTracer:
    """Dual-mode tracer:

    TRANSITION MODE (legacy):
        log_transition() records individual NodeStatus change events.
        Compatible with the original bteng API.

    FRAME MODE (new):
        begin_frame() → record_node() × N → end_frame() records one complete
        tick cycle as a TraceFrame.  Enables full replay and regression testing.

    Both modes can be used simultaneously.

    Usage::

        tracer = ExecutionTracer()
        engine = BehaviorTreeEngine(root, tracer=tracer)
        engine.run_until_complete()
        tracer.save("log.json")        # transition events
        tracer.export_json()           # frame-based JSON
        tracer.print_summary()
    """

    def __init__(self, max_frames: int = 10_000) -> None:
        # Transition-event mode (legacy)
        self._events:      List[TransitionEvent] = []
        self._enter_times: Dict[str, float]       = {}

        # Frame-based mode
        self._frames:       List[TraceFrame] = []
        self._current_frame: Optional[TraceFrame] = None
        self._in_frame:      bool = False
        self._max_frames:    int  = max_frames
        self._lock           = threading.Lock()

    # ── Transition-event mode (legacy) ────────────────────────────────────────

    def log_transition(
        self,
        node:        "TreeNode",
        prev_status: "NodeStatus",
        new_status:  "NodeStatus",
    ) -> None:
        now = time.monotonic()
        uid = node.uid
        duration_ms = 0.0
        if uid in self._enter_times:
            duration_ms = (now - self._enter_times[uid]) * 1000.0
        self._enter_times[uid] = now

        with self._lock:
            self._events.append(TransitionEvent(
                timestamp=now,
                node_name=node.name,
                node_uid=uid,
                node_type=type(node).__name__,
                prev_status=prev_status.value,
                new_status=new_status.value,
                duration_ms=round(duration_ms, 3),
            ))

    def events(self) -> List[TransitionEvent]:
        with self._lock:
            return list(self._events)

    def to_dict(self) -> list:
        return [asdict(e) for e in self.events()]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            fh.write(self.to_json())

    def print_summary(self) -> None:
        evts = self.events()
        print(f"ExecutionTracer — {len(evts)} events")
        for ev in evts:
            print(
                f"  [{ev.node_type:25s}] {ev.node_name:30s}"
                f"  {ev.prev_status:8s} → {ev.new_status:8s}"
                f"  ({ev.duration_ms:.1f} ms)"
            )

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._enter_times.clear()

    # ── Frame-based mode ──────────────────────────────────────────────────────

    def begin_frame(self, tick_index: int) -> None:
        """Mark the start of a new tick cycle (opens a frame)."""
        with self._lock:
            self._current_frame = TraceFrame(
                tick_index=tick_index,
                timestamp=time.monotonic(),
                node_records=[],
                blackboard_snapshot={},
            )
            self._in_frame = True

    def record_node(self, record: Any) -> None:
        """Record one node's execution result into the current open frame."""
        with self._lock:
            if self._in_frame and self._current_frame is not None:
                self._current_frame.node_records.append(record)

    def end_frame(self, bb_snapshot: Optional[Dict[str, Any]] = None) -> None:
        """Close the current frame, optionally attaching a blackboard snapshot."""
        with self._lock:
            if not self._in_frame or self._current_frame is None:
                return
            if bb_snapshot:
                self._current_frame.blackboard_snapshot = {
                    k: str(v) for k, v in bb_snapshot.items()
                }
            self._frames.append(self._current_frame)
            if len(self._frames) > self._max_frames:
                self._frames.pop(0)
            self._current_frame = None
            self._in_frame = False

    def frames(self) -> List[TraceFrame]:
        with self._lock:
            return list(self._frames)

    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    # ── Export ────────────────────────────────────────────────────────────────

    def export_json(self) -> str:
        """Full trace as a JSON array of frame objects."""
        frames = self.frames()
        out = []
        for f in frames:
            records = []
            for r in f.node_records:
                if hasattr(r, "__dataclass_fields__"):
                    rec = asdict(r)
                    # Convert enums to strings
                    for k, v in rec.items():
                        if hasattr(v, "value"):
                            rec[k] = v.value
                    records.append(rec)
                elif isinstance(r, dict):
                    # Already-serialised record — emit it as a JSON object, not
                    # as a Python repr string.
                    records.append(r)
                else:
                    records.append(str(r))
            out.append({
                "tick_index":          f.tick_index,
                "timestamp":           f.timestamp,
                "node_records":        records,
                "blackboard_snapshot": f.blackboard_snapshot,
            })
        return json.dumps(out, indent=2)

    def export_replay(self) -> str:
        """Compact replay format — smaller than full JSON.

        Round-trips through load_replay(): re-exporting a loaded blob yields an
        identical document.
        """
        frames = self.frames()
        compact = []
        for f in frames:
            nodes = []
            for r in f.node_records:
                entry: Dict[str, Any] = {}
                if hasattr(r, "uid"):
                    entry["uid"]  = r.uid
                if hasattr(r, "name"):
                    entry["name"] = r.name
                if hasattr(r, "node_type"):
                    nt = r.node_type
                    entry["type"] = nt.value if hasattr(nt, "value") else str(nt)
                if hasattr(r, "status"):
                    s = r.status
                    entry["status"] = s.value if hasattr(s, "value") else str(s)
                if hasattr(r, "duration"):
                    entry["dur"] = round(r.duration * 1000, 2)
                nodes.append(entry)
            compact.append({
                "t": f.tick_index,
                "ts": f.timestamp,
                "nodes": nodes,
                "bb": f.blackboard_snapshot,
            })
        return json.dumps(compact)

    # ── Replay ────────────────────────────────────────────────────────────────

    @staticmethod
    def _rehydrate_record(raw: Any) -> Any:
        """Turn one serialised node record back into a NodeExecutionRecord.

        Accepts both the compact export_replay() shape and the full
        export_json() shape.  Anything that isn't a dict is returned unchanged
        so hand-written or future blobs still load.
        """
        if not isinstance(raw, dict):
            return raw

        # Imported lazily: inspector imports from core, tracer is imported by
        # core — a module-level import would close the cycle.
        from bteng.core.node import NodeStatus, NodeType
        from bteng.introspection.inspector import NodeExecutionRecord

        def _enum(enum_cls, value, fallback):
            if isinstance(value, enum_cls):
                return value
            try:
                return enum_cls(value)
            except (ValueError, KeyError):
                pass
            try:
                return enum_cls[str(value)]
            except (KeyError, ValueError):
                return fallback

        if "duration" in raw:
            duration = raw.get("duration") or 0.0
        else:
            duration = (raw.get("dur") or 0.0) / 1000.0

        return NodeExecutionRecord(
            uid=raw.get("uid", ""),
            name=raw.get("name", ""),
            node_type=_enum(NodeType, raw.get("node_type", raw.get("type")),
                            NodeType.ACTION),
            status=_enum(NodeStatus, raw.get("status"), NodeStatus.IDLE),
            tick_time=raw.get("tick_time", 0.0) or 0.0,
            duration=duration,
            old_status=_enum(NodeStatus, raw.get("old_status"), NodeStatus.IDLE),
            feedback_message=raw.get("feedback_message", "") or "",
            halt_reason=raw.get("halt_reason", "") or "",
        )

    def load_replay(self, json_str: str) -> bool:
        """Load a previously exported replay blob (compact or full JSON).

        Node records are rehydrated into real NodeExecutionRecord objects, so a
        loaded trace re-exports identically through export_replay()/export_json().

        Returns True on success, False if the blob cannot be parsed.  On failure
        the tracer's existing frames are left untouched — the new frames are
        built in a local list and only swapped in once the whole blob has been
        read successfully.
        """
        try:
            data = json.loads(json_str)
        except Exception:
            return False

        if not isinstance(data, list):
            return False

        loaded: List[TraceFrame] = []
        try:
            for item in data:
                if not isinstance(item, dict):
                    return False
                records = item.get("nodes", item.get("node_records", []))
                if records is None:
                    records = []
                bb = item.get("bb", item.get("blackboard_snapshot", {})) or {}
                loaded.append(TraceFrame(
                    tick_index=item.get("t", item.get("tick_index", 0)),
                    timestamp=item.get("ts", item.get("timestamp", 0.0)),
                    node_records=[self._rehydrate_record(r) for r in records],
                    blackboard_snapshot=bb,
                ))
        except Exception:
            return False

        with self._lock:
            if self._max_frames >= 0 and len(loaded) > self._max_frames:
                del loaded[: len(loaded) - self._max_frames]
            self._frames = loaded
        return True

    def replay_frame(self, index: int) -> Optional[TraceFrame]:
        """Return frame at index (from loaded replay), or None if out of range."""
        with self._lock:
            if 0 <= index < len(self._frames):
                return self._frames[index]
            return None

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._enter_times.clear()
            self._frames.clear()
            self._current_frame = None
            self._in_frame = False

    def set_max_frames(self, n: int) -> None:
        """Set the frame ring-buffer bound, trimming anything already over it.

        Oldest frames are dropped first, matching end_frame()'s eviction order.
        """
        with self._lock:
            self._max_frames = n
            if n >= 0 and len(self._frames) > n:
                del self._frames[: len(self._frames) - n]
