"""Structured event logger for node state transitions."""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Deque, List, Optional, Tuple

from bteng.core.node import NodeID, NodeStatus


# ── LogLevel ──────────────────────────────────────────────────────────────────

class LogLevel(Enum):
    DEBUG = 0
    INFO  = 1
    WARN  = 2
    ERROR = 3


# ── LogEntry ──────────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    """Immutable record of a single node state transition."""
    timestamp:  float       # time.monotonic()
    level:      LogLevel
    node_uid:   NodeID
    node_name:  str
    old_status: NodeStatus
    new_status: NodeStatus
    message:    str   = ""
    reason:     str   = ""   # failure or halt reason
    duration:   float = 0.0  # wall-clock duration of the tick (seconds)


# ── ANSI color helpers ────────────────────────────────────────────────────────

_ANSI = {
    NodeStatus.SUCCESS: "\033[32m",  # green
    NodeStatus.FAILURE: "\033[31m",  # red
    NodeStatus.RUNNING: "\033[33m",  # yellow
    NodeStatus.IDLE:    "\033[0m",   # reset
}
_RESET = "\033[0m"


def _status_colored(status: NodeStatus, colored: bool) -> str:
    s = status.value
    if colored:
        return f"{_ANSI.get(status, '')}{s}{_RESET}"
    return s


# ── Logger ────────────────────────────────────────────────────────────────────

class Logger:
    """Structured event logger with pluggable sinks.

    Sinks receive every LogEntry that passes the minimum level filter.
    Multiple sinks can be active simultaneously.

    Usage::

        logger = Logger.create()
        logger.add_console_sink(colored=True)
        logger.add_json_file_sink("/tmp/bt_run.jsonl")

        executor = TreeExecutor(config)
        executor.set_logger(logger)
        ...
        logger.close()        # releases file sinks' descriptors

    File sinks own an open descriptor, so a Logger that opened one must be
    closed.  Logger is also a context manager::

        with Logger.create() as logger:
            logger.add_json_file_sink("/tmp/bt_run.jsonl")
            ...
    """

    Sink = Callable[["LogEntry"], None]

    @classmethod
    def create(cls) -> "Logger":
        return cls()

    def __init__(self, max_history: int = 10_000) -> None:
        self._sinks:       List["Logger.Sink"]  = []
        self._history:     Deque[LogEntry]      = deque(maxlen=max_history)
        self._min_level:   LogLevel             = LogLevel.INFO
        self._max_history: int                  = max_history
        self._lock         = threading.Lock()
        # (file handle, sink) pairs opened by add_json_file_sink(), so close()
        # can release the descriptors and detach the sinks that use them.
        self._file_sinks:  List[Tuple[Any, "Logger.Sink"]] = []

    # ── Writing ───────────────────────────────────────────────────────────────

    def log(self, entry: LogEntry) -> None:
        """Low-level entry point. Filters by min_level, fans out to sinks."""
        if entry.level.value < self._min_level.value:
            return
        if entry.timestamp == 0.0:
            entry.timestamp = time.monotonic()

        with self._lock:
            self._history.append(entry)   # deque with maxlen handles eviction
            sinks_copy = list(self._sinks)

        for sink in sinks_copy:
            try:
                sink(entry)
            except Exception as exc:
                print(f"[bteng] Logger sink raised: {exc}", file=sys.stderr)

    def log_transition(
        self,
        uid:       NodeID,
        name:      str,
        from_:     NodeStatus,
        to:        NodeStatus,
        duration:  float = 0.0,
        reason:    str   = "",
    ) -> None:
        """Convenience: record a node status transition (the common case)."""
        level = LogLevel.DEBUG
        if to in (NodeStatus.SUCCESS, NodeStatus.FAILURE):
            level = LogLevel.INFO
        self.log(LogEntry(
            timestamp=time.monotonic(), level=level,
            node_uid=uid, node_name=name,
            old_status=from_, new_status=to,
            duration=duration, reason=reason,
        ))

    # ── Sinks ─────────────────────────────────────────────────────────────────

    def add_json_file_sink(self, path: str) -> "Logger.Sink":
        """Append JSON-lines to a file.  Creates/appends to the file.

        The descriptor is owned by this Logger: call close() (or use the Logger
        as a context manager) to release it.  Returns the sink callable so it
        can be detached individually via remove_sink().
        """
        fh = open(path, "a", encoding="utf-8")

        def sink(entry: LogEntry) -> None:
            fh.write(self._entry_to_json(entry) + "\n")
            fh.flush()

        with self._lock:
            self._file_sinks.append((fh, sink))
        self.add_custom_sink(sink)
        return sink

    def add_console_sink(self, colored: bool = True, stream: Any = None) -> "Logger.Sink":
        """Print entries to stdout (or given stream).

        Returns the sink callable (for remove_sink); owns no descriptor.
        """
        out = stream or sys.stdout
        _colored = colored

        def sink(entry: LogEntry) -> None:
            old = entry.old_status.value
            new = _status_colored(entry.new_status, _colored)
            dur = f"{entry.duration * 1000:.1f}ms"
            msg = (
                f"[{entry.level.name:5s}] "
                f"{entry.node_name:30s}  "
                f"{old:8s} → {new:20s}  "
                f"({dur})"
            )
            if entry.reason:
                msg += f"  reason={entry.reason!r}"
            print(msg, file=out)

        self.add_custom_sink(sink)
        return sink

    def add_custom_sink(self, sink: "Logger.Sink") -> "Logger.Sink":
        with self._lock:
            self._sinks.append(sink)
        return sink

    def remove_sink(self, sink: "Logger.Sink") -> bool:
        """Detach a previously added sink, closing its file handle if it owns one.

        Returns True if the sink was registered, False otherwise.
        """
        handle = None
        with self._lock:
            try:
                self._sinks.remove(sink)
            except ValueError:
                return False
            for i, (fh, s) in enumerate(self._file_sinks):
                if s is sink:
                    handle = fh
                    del self._file_sinks[i]
                    break
        if handle is not None:
            try:
                handle.close()
            except Exception as exc:
                print(f"[bteng] Logger sink close failed: {exc}", file=sys.stderr)
        return True

    def close(self) -> None:
        """Detach all file sinks and close their descriptors.

        Idempotent.  Non-file sinks (console, custom) are left registered, so
        logging after close() still works — it simply no longer writes to files.
        """
        with self._lock:
            pairs = self._file_sinks
            self._file_sinks = []
            for _fh, sink in pairs:
                try:
                    self._sinks.remove(sink)
                except ValueError:
                    pass
        for fh, _sink in pairs:
            try:
                fh.close()
            except Exception as exc:
                print(f"[bteng] Logger sink close failed: {exc}", file=sys.stderr)

    def __enter__(self) -> "Logger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def set_min_level(self, level: LogLevel) -> None:
        self._min_level = level

    # ── In-memory history ─────────────────────────────────────────────────────

    def history(self) -> List[LogEntry]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    def set_max_history(self, n: int) -> None:
        with self._lock:
            self._max_history = n
            self._history = deque(self._history, maxlen=n)

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _entry_to_json(entry: LogEntry) -> str:
        return json.dumps({
            "ts":      entry.timestamp,
            "level":   entry.level.name,
            "uid":     entry.node_uid,
            "name":    entry.node_name,
            "from":    entry.old_status.value,
            "to":      entry.new_status.value,
            "dur_ms":  round(entry.duration * 1000, 3),
            "reason":  entry.reason,
            "message": entry.message,
        })

    def __repr__(self) -> str:
        return f"Logger(sinks={len(self._sinks)}, history={len(self._history)})"
