"""Scoped, observable key-value store for inter-node communication."""
from __future__ import annotations

import sys
import threading
import time
import weakref
from collections import deque
from copy import copy
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Deque, Dict, List, Optional, Tuple

from bteng.core.node import PortDirection


# ── Sentinel for "no value set" (distinct from storing None explicitly) ───────

class _Unset:
    """Singleton sentinel distinguishing 'key not set' from 'key set to None'."""
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self) -> str:
        return "<UNSET>"

_UNSET = _Unset()


# ── History record ────────────────────────────────────────────────────────────

@dataclass
class BlackboardHistoryRecord:
    """Snapshot of a previous value before it was overwritten."""
    value:     Any
    writer:    str   # NodeID that wrote the previous value
    timestamp: float  # time.monotonic() when it was written


# ── Blackboard entry ──────────────────────────────────────────────────────────

@dataclass
class BlackboardEntry:
    """Internal storage cell for one blackboard key."""
    value:       Any          = field(default_factory=_Unset)
    type_name:   str          = ""
    last_writer: str          = ""   # NodeID
    last_write_time: float    = 0.0
    history:     Deque[BlackboardHistoryRecord] = field(default_factory=deque)
    MAX_HISTORY: ClassVar[int] = 32

    def has_value(self) -> bool:
        return self.value is not _UNSET


# ── Port schema ───────────────────────────────────────────────────────────────

@dataclass
class PortSchema:
    """Optional schema entry for validating blackboard keys."""
    name:        str
    type_hint:   Optional[type] = None
    direction:   PortDirection  = PortDirection.INPUT
    required:    bool           = False
    description: str            = ""


# ── Blackboard ────────────────────────────────────────────────────────────────

class Blackboard:
    """Scoped, observable key-value store for inter-node communication.

    Key design decisions:

    SCOPED HIERARCHY:
        Child scopes fall through to their parent for unknown keys.
        Subtrees get their own scope (via create_child_scope) so their internal
        keys don't pollute the parent tree's namespace.

    READS INHERIT, WRITES DO NOT (deliberate asymmetry):
        ``get()``/``has()`` fall through to the parent scope for any key that is
        not set locally, but ``set()`` always writes into *this* scope unless the
        key is explicitly remapped.  A child that does a read-modify-write on an
        inherited key therefore *shadows* it: the child sees the new value, the
        parent keeps the old one.  This is intentional — a subtree must not be
        able to clobber its parent's namespace by accident — but it means that
        keys a subtree wants to write back must be declared in ``remapping``.

    PORT REMAPPING:
        ``set``/``get``/``has``/``remove``/``delete``/``entry`` honour
        ``remapping`` and operate on the parent's key.  The bulk/introspection
        operations ``clear``/``keys``/``snapshot``/``take_snapshot_if_dirty``/
        ``debug_string`` are deliberately *scope-local*: they report and mutate
        only this scope's own entries and never touch the parent, so a remapped
        (or merely inherited) key that ``get()`` resolves is not listed by them.

    PROVENANCE HISTORY:
        Every write records a ring buffer (max 32 entries) of past values
        along with the writer's NodeID and timestamp.  Supports replay debugging.

    CHANGE SUBSCRIPTIONS:
        Callers may subscribe to any key write.  Used by the reactive execution
        model to trigger re-evaluation when condition-related values change.

    THREAD SAFETY:
        Protected by a reentrant lock.  Multiple nodes may read concurrently
        under the same tick since Python's GIL provides implicit protection.
    """

    MAX_HISTORY = 32

    _global_instances: Dict[str, "Blackboard"] = {}
    _global_lock = threading.Lock()

    def __init__(
        self,
        scope_name: str = "root",
        parent: Optional["Blackboard"] = None,
        remapping: Optional[Dict[str, str]] = None,
    ) -> None:
        self._scope_name  = scope_name
        self._parent      = parent
        self._remapping:  Dict[str, str]           = remapping or {}
        self._entries:    Dict[str, BlackboardEntry]  = {}
        self._schema:     Dict[str, PortSchema]    = {}
        self._callbacks:  Dict[int, Callable[[str, Any], None]] = {}
        self._next_cb_id: int = 0
        self._callbacks_cache: list = []
        self._callbacks_dirty: bool = True
        self._lock        = threading.RLock()
        self._written_since_snapshot: bool = False

    # ── Class-level factory ───────────────────────────────────────────────────

    @classmethod
    def create(cls, name: str = "__global__") -> "Blackboard":
        with cls._global_lock:
            if name not in cls._global_instances:
                cls._global_instances[name] = cls(scope_name=name)
            return cls._global_instances[name]

    @classmethod
    def reset(cls, name: str = "__global__") -> None:
        """Clear a named global blackboard (useful between test runs).

        Clears entries, callbacks (including the dispatch cache), port schema
        and the dirty-snapshot flag so tests — and long-lived processes that
        rebuild a tree against the same blackboard name — do not poison each
        other with stale state.
        """
        with cls._global_lock:
            if name in cls._global_instances:
                bb = cls._global_instances[name]
                with bb._lock:
                    bb._entries.clear()
                    bb._callbacks.clear()
                    # The dispatch cache is only rebuilt when the dirty flag is
                    # set, so clearing _callbacks alone leaves every previously
                    # cached subscriber live (and its closure un-collectable).
                    # Rebind rather than .clear() so a concurrent set() that is
                    # already iterating the old list is unaffected.
                    bb._callbacks_cache = []
                    bb._callbacks_dirty = True
                    bb._schema.clear()
                    bb._written_since_snapshot = False

    @classmethod
    def registered_names(cls) -> List[str]:
        """Names of every global blackboard created so far via :meth:`create`."""
        with cls._global_lock:
            return list(cls._global_instances)

    @classmethod
    def reset_all(cls) -> None:
        """Clear every named global blackboard.

        ``create(name)`` returns a process-wide singleton, so state written by one
        test is visible to the next unless it is cleared. This resets all of them
        in one call — see ``bteng.testing.plugin`` for a pytest fixture that does
        it automatically between tests.
        """
        for name in cls.registered_names():
            cls.reset(name)

    @classmethod
    def create_child(
        cls,
        parent: "Blackboard",
        remapping: Optional[Dict[str, str]] = None,
    ) -> "Blackboard":
        """Create a child scope with port remapping (legacy API)."""
        return cls(scope_name="child", parent=parent, remapping=remapping)

    def create_child_scope(
        self,
        scope_name: str,
        remapping: Optional[Dict[str, str]] = None,
    ) -> "Blackboard":
        """Create a named child scope.

        Keys in remapping are redirected to this blackboard's keys.
        All other key reads fall through to this (parent) scope.
        Used when entering a subtree.
        """
        return Blackboard(scope_name=scope_name, parent=self, remapping=remapping)

    # ── Core read/write ───────────────────────────────────────────────────────

    def set(self, key: str, value: Any, writer: str = "") -> None:
        """Write a value to the blackboard.

        Stores the previous value in the history buffer (max 32 entries).
        Notifies all change subscribers after releasing the lock.
        """
        with self._lock:
            actual_key = self._remapping.get(key)
            if actual_key is not None and self._parent is not None:
                self._parent.set(actual_key, value, writer=writer)
                return

            entry = self._entries.setdefault(key, BlackboardEntry())

            # Archive current value into history before overwriting
            if entry.has_value():
                rec = BlackboardHistoryRecord(
                    value=entry.value,
                    writer=entry.last_writer,
                    timestamp=entry.last_write_time,
                )
                entry.history.append(rec)
                if len(entry.history) > self.MAX_HISTORY:
                    entry.history.popleft()

            entry.value          = value
            entry.type_name      = type(value).__name__
            entry.last_writer    = writer
            entry.last_write_time = time.monotonic()
            self._written_since_snapshot = True

            if self._callbacks_dirty:
                self._callbacks_cache = list(self._callbacks.items())
                self._callbacks_dirty = False
            cbs = self._callbacks_cache

        dead: List[int] = []
        for cb_id, holder in cbs:
            if isinstance(holder, weakref.ref):
                cb = holder()          # WeakMethod → rebuilt bound method
                if cb is None:         # owner was garbage collected
                    dead.append(cb_id)
                    continue
            else:
                cb = holder
            try:
                cb(key, value)
            except Exception as exc:
                print(f"[bteng] Blackboard subscriber raised: {exc}", file=sys.stderr)

        if dead:
            self._reap(dead)

    def _reap(self, dead_ids: List[int]) -> None:
        """Drop subscriptions whose weakly-held owner has been collected."""
        with self._lock:
            for cb_id in dead_ids:
                holder = self._callbacks.get(cb_id)
                if isinstance(holder, weakref.ref) and holder() is None:
                    del self._callbacks[cb_id]
                    self._callbacks_dirty = True

    def get(self, key: str, default: Any = None) -> Any:
        """Read a value from the blackboard.

        Falls through to the parent scope if the key is not found locally.
        """
        with self._lock:
            actual_key = self._remapping.get(key)
            if actual_key is not None and self._parent is not None:
                return self._parent.get(actual_key, default)
            if key in self._entries and self._entries[key].has_value():
                return self._entries[key].value
            # Fall through to parent for unknown keys (subtree → parent lookup)
            if self._parent is not None:
                return self._parent.get(key, default)
            return default

    def get_or_default(self, key: str, default: Any) -> Any:
        """Read a value, returning default if absent."""
        return self.get(key, default)

    def has(self, key: str) -> bool:
        with self._lock:
            actual_key = self._remapping.get(key)
            if actual_key is not None and self._parent is not None:
                return self._parent.has(actual_key)
            if key in self._entries and self._entries[key].has_value():
                return True
            if self._parent is not None:
                return self._parent.has(key)
            return False

    def remove(self, key: str) -> None:
        """Delete a key.  Honours remapping — remove() is the inverse of set().

        A remapped key is removed from the parent scope, exactly as set()/get()
        would have written/read it there.  A non-remapped key is removed from
        this scope only; inherited parent keys are not touched (mirroring set(),
        which never writes to the parent either).
        """
        with self._lock:
            actual_key = self._remapping.get(key)
            if actual_key is not None and self._parent is not None:
                self._parent.remove(actual_key)
                return
            self._entries.pop(key, None)

    # backward-compat alias
    def delete(self, key: str) -> None:
        """Alias for remove(); honours remapping the same way."""
        self.remove(key)

    def clear(self) -> None:
        """Remove all entries from this scope.

        Deliberately scope-local: neither the parent's entries nor the targets
        of this scope's remappings are removed, so clear() cannot let a subtree
        wipe its parent's namespace.  A remapped key therefore remains readable
        through get() after clear() — use remove(key) to delete it.
        """
        with self._lock:
            self._entries.clear()

    # ── Entry inspection ──────────────────────────────────────────────────────

    def entry(self, key: str) -> Optional[BlackboardEntry]:
        """Return a snapshot copy of the entry for a key, or None if absent.

        Honours remapping: a remapped key resolves to the parent's entry, so
        entry() sees the same cell that get()/set() use.  Non-remapped keys are
        looked up in this scope only — an inherited parent key that get() would
        resolve still yields None here, matching keys()/snapshot().

        Returns a copy so callers cannot mutate internal state outside the lock.
        The history is returned as a new deque (shared items, not a deep copy).
        """
        with self._lock:
            actual_key = self._remapping.get(key)
            if actual_key is not None and self._parent is not None:
                return self._parent.entry(actual_key)
            e = self._entries.get(key)
            if e is None:
                return None
            snapshot = BlackboardEntry(
                value=e.value,
                type_name=e.type_name,
                last_writer=e.last_writer,
                last_write_time=e.last_write_time,
                history=deque(e.history),
            )
            return snapshot

    def keys(self) -> List[str]:
        """All keys stored in this scope.

        Deliberately scope-local: inherited parent keys and remapped keys (whose
        storage lives in the parent) are NOT listed, even though get() resolves
        them.  Walk `_parent` yourself if you need the effective key set.
        """
        with self._lock:
            return list(self._entries.keys())

    def snapshot(self) -> Dict[str, Any]:
        """Current values of all keys stored in this scope.

        Scope-local, same rule as keys(): inherited and remapped keys are not
        included.  Keeps a subtree's trace/telemetry to its own namespace.
        """
        with self._lock:
            return {k: e.value for k, e in self._entries.items() if e.has_value()}

    def take_snapshot_if_dirty(self) -> Optional[Dict[str, Any]]:
        """Scope-local snapshot() if this scope was written since the last call.

        Returns None when nothing changed.  A write that was redirected to the
        parent by remapping dirties the *parent*, not this scope.
        """
        with self._lock:
            if not self._written_since_snapshot:
                return None
            self._written_since_snapshot = False
            return {k: e.value for k, e in self._entries.items() if e.has_value()}

    @property
    def scope_name(self) -> str:
        return self._scope_name

    # ── Change subscriptions ──────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[str, Any], None]) -> int:
        """Register a listener for any key write.

        Returns a subscription ID for later removal via unsubscribe().
        Callbacks are invoked after the write lock is released with
        (key, new_value) arguments.

        REFERENCE SEMANTICS — read this before subscribing:

        * A **bound method** (``obj.handler``) is held *weakly*, via
          ``weakref.WeakMethod``.  Subscribing does not keep ``obj`` alive; once
          ``obj`` is collected the subscription is silently dropped at the next
          write.  This is what stops a RUNNING ``ReactiveSequence``/
          ``ReactiveFallback`` that is discarded without being halted (tree
          swapped, goal aborted, executor torn down) from pinning itself and its
          whole subtree in memory — and from being invoked on every subsequent
          write, forever.
        * **Every other callable** — plain functions, lambdas, closures,
          ``functools.partial``, callable instances — is held *strongly*, exactly
          as before.  Callers routinely pass a throwaway lambda and keep no
          reference to it; dropping those would be a far worse bug than the leak.
          Such subscriptions live until ``unsubscribe()`` (or ``Blackboard.reset``).
        * A bound method of an object that does not support weak references
          (e.g. ``__slots__`` without ``__weakref__``) also falls back to a
          strong reference.

        So: if you subscribe a bound method, keep a reference to the object for
        as long as you want the callback to fire.
        """
        with self._lock:
            cb_id = self._next_cb_id
            self._next_cb_id += 1
            self._callbacks[cb_id] = self._hold(callback)
            self._callbacks_dirty = True
            return cb_id

    @staticmethod
    def _hold(callback: Callable[[str, Any], None]) -> Any:
        """Wrap a callback for storage: bound methods weakly, everything else not."""
        if hasattr(callback, "__self__") and hasattr(callback, "__func__"):
            try:
                return weakref.WeakMethod(callback)
            except TypeError:
                # Owner is not weak-referenceable — keep the strong reference.
                return callback
        return callback

    def unsubscribe(self, callback_id: int) -> None:
        with self._lock:
            self._callbacks.pop(callback_id, None)
            self._callbacks_dirty = True

    # ── Port schema ───────────────────────────────────────────────────────────

    def register_port_schema(self, schema: PortSchema) -> None:
        """Register an expected port for validation."""
        with self._lock:
            self._schema[schema.name] = schema

    def validate_against_schema(self) -> Tuple[bool, str]:
        """Check that all required schema keys are present.

        Returns (True, "") on success or (False, error_message) on failure.
        """
        with self._lock:
            for name, schema in self._schema.items():
                if schema.required:
                    if name not in self._entries or not self._entries[name].has_value():
                        return False, f"Required blackboard key '{name}' is missing"
                    if schema.type_hint is not None:
                        val = self._entries[name].value
                        if not isinstance(val, schema.type_hint):
                            return False, (
                                f"Key '{name}' expected {schema.type_hint.__name__}, "
                                f"got {type(val).__name__}"
                            )
        return True, ""

    # ── Debug ─────────────────────────────────────────────────────────────────

    def debug_string(self) -> str:
        """Human-readable dump of the entries stored in this scope.

        Scope-local (see keys()): inherited and remapped keys are not shown.
        """
        with self._lock:
            lines = [f"Blackboard[{self._scope_name}]"]
            for key, entry in sorted(self._entries.items()):
                if entry.has_value():
                    lines.append(
                        f"  {key!r:30s} = {entry.value!r:40s} "
                        f"(type={entry.type_name}, writer={entry.last_writer!r})"
                    )
            return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Blackboard(scope={self._scope_name!r}, keys={self.keys()})"
