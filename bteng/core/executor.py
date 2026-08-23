"""TreeExecutor — the runtime loop that drives a behavior tree."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from bteng.core.node import NodeStatus, TreeNode
from bteng.core.tree import Tree
from bteng.blackboard.blackboard import Blackboard


# ── ExecutorConfig ────────────────────────────────────────────────────────────

@dataclass
class ExecutorConfig:
    """Tuning parameters for the TreeExecutor."""
    tick_interval:     float = 0.010   # seconds (10 ms) — target period for event loop
    #: worker threads in the ThreadPool the executor builds for AsyncActionNodes.
    #: The pool is created lazily, only when the tree actually contains a node
    #: that wants one, and is shut down by :meth:`TreeExecutor.shutdown`.
    #: ``0`` disables it — those nodes then each spawn their own daemon thread.
    thread_pool_size:  int   = 4
    enable_tracing:    bool  = True
    enable_logging:    bool  = True
    halt_on_completion: bool = True    # auto-halt tree when root returns SUCCESS/FAILURE


# ── EventBus ──────────────────────────────────────────────────────────────────

@dataclass
class BehaviorEvent:
    """Named behavioral event dispatched through the EventBus."""
    name:      str
    payload:   Any    = None
    timestamp: float  = field(default_factory=time.monotonic)
    source_uid: str   = ""


class EventBus:
    """Lightweight publish-subscribe channel for named behavioral events.

    Subscribers can use "*" as event_name to receive all events.
    Callbacks are invoked synchronously on the publishing thread —
    keep them short and non-blocking.

    Usage::

        bus = EventBus.create()
        sub_id = bus.subscribe("obstacle_detected", lambda e: print(e))
        bus.publish(BehaviorEvent(name="obstacle_detected", payload={"dist": 0.5}))
        bus.unsubscribe(sub_id)
    """

    @classmethod
    def create(cls) -> "EventBus":
        return cls()

    def __init__(self) -> None:
        self._subs:     Dict[int, tuple] = {}  # id → (event_name, callback)
        self._next_id:  int = 0
        self._lock      = threading.Lock()

    def publish(self, event: BehaviorEvent) -> None:
        """Dispatch event to all matching subscribers."""
        import sys
        with self._lock:
            subs_copy = list(self._subs.items())
        for _, (evt_name, cb) in subs_copy:
            if evt_name in ("*", event.name):
                try:
                    cb(event)
                except Exception as exc:
                    print(f"[bteng] EventBus subscriber raised: {exc}", file=sys.stderr)

    def subscribe(self, event_name: str, callback: Callable[[BehaviorEvent], None]) -> int:
        """Register a listener. Returns subscription ID for later removal."""
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs[sub_id] = (event_name, callback)
            return sub_id

    def unsubscribe(self, subscription_id: int) -> None:
        with self._lock:
            self._subs.pop(subscription_id, None)

    def __repr__(self) -> str:
        return f"EventBus(subscribers={len(self._subs)})"


# ── TreeExecutor ──────────────────────────────────────────────────────────────

class TreeExecutor:
    """Runtime loop that drives a behavior tree.

    Supports three execution modes:

      1. MANUAL  (tick_once / tick_until_result)
         Caller drives the tick rate.  Good for unit tests, step-through
         debugging, and simulation environments.

      2. EVENT LOOP  (start_event_loop / stop_event_loop)
         Background thread ticks at config.tick_interval.
         Suitable for real-time applications.

      3. REACTIVE (future)
         Re-evaluates only when subscribed blackboard keys change.
         Currently approximated by the event loop + condition subscriptions.

    Usage::

        executor = TreeExecutor(ExecutorConfig(tick_interval=0.02))
        executor.set_tree(tree)
        executor.set_logger(logger)

        # Manual mode:
        status = executor.tick_until_result(max_ticks=1000)

        # Event-loop mode:
        executor.start_event_loop()
        executor.on_completion(lambda s: print("Done:", s))
        ...
        executor.stop_event_loop()
    """

    CompletionCallback = Callable[[NodeStatus], None]

    def __init__(self, config: Optional[ExecutorConfig] = None) -> None:
        self._config    = config or ExecutorConfig()
        self._tree:     Optional[Tree]     = None
        self._logger                       = None   # Logger (avoid import cycle)
        self._inspector                    = None   # Inspector
        self._tracer                       = None   # ExecutionTracer
        self._event_bus: Optional[EventBus] = None
        self._thread_pool                  = None   # ThreadPool
        self._owns_thread_pool: bool       = False  # True → shutdown() disposes it
        self._completion_cb: Optional["TreeExecutor.CompletionCallback"] = None

        self._loop_thread:   Optional[threading.Thread] = None
        self._loop_running   = threading.Event()
        self._paused         = threading.Event()
        self._pause_lock     = threading.Lock()
        self._tick_count:    int = 0
        self._final_status:  Optional[NodeStatus] = None
        self._logger_sub_id: Optional[int] = None  # inspector subscription for logger
        self._nodes_setup:   bool = False
        # uids already given setup(); tracked here rather than on the nodes so a
        # subtree installed by a runtime modification can be set up later without
        # setup() ever running twice on a node that already has it.
        self._setup_uids:    set = set()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def set_tree(self, tree: Tree) -> None:
        tree.validate()
        self._tree = tree
        self._nodes_setup = False
        self._setup_uids = set()
        if self._inspector is not None:
            self._inject_inspector(self._tree.root, self._inspector)
        if self._tracer is not None:
            self._inject_tracer(self._tree.root, self._tracer)
        if self._thread_pool is not None:
            self._inject_thread_pool(self._tree.root, self._thread_pool)

    def set_logger(self, logger: Any) -> None:
        self._logger = logger
        self._wire_logger()

    def set_inspector(self, inspector: Any) -> None:
        self._inspector = inspector
        if self._tree is not None:
            self._inject_inspector(self._tree.root, inspector)
        self._wire_logger()

    def set_tracer(self, tracer: Any) -> None:
        self._tracer = tracer
        if self._tree is not None:
            self._inject_tracer(self._tree.root, tracer)

    def set_event_bus(self, bus: EventBus) -> None:
        self._event_bus = bus

    def set_thread_pool(self, pool: Any) -> None:
        """Supply a ThreadPool explicitly.

        The caller keeps ownership — :meth:`shutdown` will not dispose it.  Any
        pool the executor built for itself is disposed here instead of leaking.
        """
        if self._owns_thread_pool and self._thread_pool is not None and self._thread_pool is not pool:
            self._thread_pool.shutdown()
        self._thread_pool = pool
        self._owns_thread_pool = False
        if self._tree is not None:
            self._inject_thread_pool(self._tree.root, pool)

    def on_completion(self, callback: "TreeExecutor.CompletionCallback") -> None:
        """Set a callback invoked once when tree reaches SUCCESS or FAILURE."""
        self._completion_cb = callback

    # ── Manual execution ──────────────────────────────────────────────────────

    def tick_once(self) -> NodeStatus:
        """Apply pending modifications and tick the root node once."""
        if self._tree is None:
            raise RuntimeError("TreeExecutor: no tree set (call set_tree first)")
        return self._do_tick()

    def tick_until_result(self, max_ticks: int = 0) -> NodeStatus:
        """Tick repeatedly until root returns SUCCESS or FAILURE.

        Sleeps config.tick_interval between ticks so async background threads
        have time to complete.  max_ticks=0 means unlimited.
        """
        count = 0
        while True:
            status = self.tick_once()
            count += 1
            if status != NodeStatus.RUNNING:
                # Recorded here as well as in the event loop: final_status is
                # documented without an "event-loop only" caveat, and callers
                # (including bteng's own stress tests) read it after a manual
                # tick_until_result().
                self._final_status = status
                if self._config.halt_on_completion:
                    self._tree.halt_all()
                if self._completion_cb:
                    self._completion_cb(status)
                return status
            if max_ticks and count >= max_ticks:
                return status
            if self._config.tick_interval > 0:
                time.sleep(self._config.tick_interval)

    # ── Event loop (background thread) ────────────────────────────────────────

    def start_event_loop(self) -> None:
        """Start ticking in a background thread at config.tick_interval."""
        if self._loop_running.is_set():
            return
        self._loop_running.set()
        self._paused.clear()
        self._loop_thread = threading.Thread(
            target=self._event_loop_body,
            daemon=True,
            name="bteng-executor",
        )
        self._loop_thread.start()

    def stop_event_loop(self) -> None:
        """Stop the background event loop and wait for it to finish."""
        self._loop_running.clear()
        self._paused.clear()   # unblock pause if suspended
        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5.0)
        self._loop_thread = None
        self.shutdown()

    def shutdown(self) -> None:
        """Call shutdown() on all nodes and mark them as needing re-setup.

        Also disposes the ThreadPool if this executor built it; a pool passed in
        through :meth:`set_thread_pool` belongs to the caller and is left alone.

        Call after tick_until_result() or when permanently done with this
        executor.  Safe to call multiple times — no-op if nodes are not set up.
        """
        if self._tree is not None and self._nodes_setup:
            self._shutdown_nodes(self._tree.root)
            self._nodes_setup = False
        if self._owns_thread_pool and self._thread_pool is not None:
            pool, self._thread_pool = self._thread_pool, None
            self._owns_thread_pool = False
            if self._tree is not None:
                self._inject_thread_pool(self._tree.root, None)
            pool.shutdown()

    def is_running(self) -> bool:
        return self._loop_running.is_set()

    # ── Flow control ──────────────────────────────────────────────────────────

    def pause(self) -> None:
        """Suspend the event loop (does not halt running nodes)."""
        self._paused.set()

    def resume(self) -> None:
        """Resume after pause()."""
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def halt_tree(self) -> None:
        """Halt all running nodes; tree stays loaded."""
        if self._tree is not None:
            self._tree.halt_all()

    def reset_tree(self) -> None:
        """Reset all nodes to IDLE."""
        if self._tree is not None:
            self._tree.reset_all()

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def tree(self) -> Optional[Tree]:
        return self._tree

    @property
    def logger(self) -> Any:
        return self._logger

    @property
    def inspector(self) -> Any:
        return self._inspector

    @property
    def tracer(self) -> Any:
        return self._tracer

    @property
    def event_bus(self) -> Optional[EventBus]:
        return self._event_bus

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def final_status(self) -> Optional[NodeStatus]:
        return self._final_status

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_tick(self) -> NodeStatus:
        if self._tree is None:
            raise RuntimeError("TreeExecutor: no tree set (call set_tree first)")

        if not self._nodes_setup:
            self._ensure_thread_pool()
            self._setup_nodes(self._tree.root)
            self._nodes_setup = True
        elif getattr(self._tree, "_pending_mods", None):
            # A modification is about to install nodes that missed the initial
            # pass. setup() is documented to run "after all injections are in
            # place", and that promise has to hold for hot-swapped subtrees too:
            # otherwise they tick with unopened handles, no tracer, no inspector
            # and no thread pool. Re-walking is cheap and the injectors are
            # idempotent; _setup_uids keeps setup() itself exactly-once per node.
            self._tree.apply_pending_modifications()
            self._reinject_all()
            self._setup_nodes(self._tree.root)

        if self._tracer is not None:
            self._tracer.begin_frame(self._tick_count)

        status = self._tree.tick_once()
        self._tick_count += 1

        if self._tracer is not None:
            bb_snapshot = self._tree.blackboard.take_snapshot_if_dirty()
            if bb_snapshot is not None:
                self._tracer.end_frame({k: str(v) for k, v in bb_snapshot.items()})

        return status

    def _event_loop_body(self) -> None:
        import sys
        while self._loop_running.is_set():
            # Respect pause
            if self._paused.is_set():
                time.sleep(0.005)
                continue

            try:
                status = self._do_tick()
            except Exception as exc:
                print(f"[bteng] TreeExecutor: unhandled exception in tick loop: {exc}", file=sys.stderr)
                self._final_status = NodeStatus.FAILURE
                if self._completion_cb:
                    self._completion_cb(NodeStatus.FAILURE)
                break

            if status != NodeStatus.RUNNING:
                self._final_status = status
                if self._config.halt_on_completion:
                    self._tree.halt_all()
                self._loop_running.clear()
                self.shutdown()
                if self._completion_cb:
                    self._completion_cb(status)
                break

            time.sleep(self._config.tick_interval)

    def _setup_nodes(self, node: TreeNode) -> None:
        import sys
        if node.uid not in self._setup_uids:
            self._setup_uids.add(node.uid)
            try:
                node.setup()
            except Exception as exc:
                print(f"[bteng] setup() raised on {node.name!r}: {exc}", file=sys.stderr)
        for child in node.get_children():
            self._setup_nodes(child)

    def _reinject_all(self) -> None:
        """Re-run every injector over the current tree.

        Called after a runtime modification so newly installed nodes receive the
        tracer, inspector and thread pool the rest of the tree already has.
        """
        if self._tree is None:
            return
        if self._inspector is not None:
            self._inject_inspector(self._tree.root, self._inspector)
        if self._tracer is not None:
            self._inject_tracer(self._tree.root, self._tracer)
        if self._thread_pool is not None:
            self._inject_thread_pool(self._tree.root, self._thread_pool)

    def _shutdown_nodes(self, node: TreeNode) -> None:
        import sys
        self._setup_uids.discard(node.uid)
        try:
            node.shutdown()
        except Exception as exc:
            print(f"[bteng] shutdown() raised on {node.name!r}: {exc}", file=sys.stderr)
        for child in node.get_children():
            self._shutdown_nodes(child)

    def _inject_tracer(self, node: TreeNode, tracer: Any) -> None:
        node._tracer = tracer
        for child in node.get_children():
            self._inject_tracer(child, tracer)

    def _inject_inspector(self, node: TreeNode, inspector: Any) -> None:
        node._inspector = inspector
        for child in node.get_children():
            self._inject_inspector(child, inspector)

    def _ensure_thread_pool(self) -> None:
        """Build and inject the shared ThreadPool, if the tree needs one.

        Lazy on purpose: a tree of plain synchronous nodes should not pay for
        four idle worker threads, and a tree of CoroActionNodes runs its work on
        an event loop instead.
        """
        if self._thread_pool is not None or self._config.thread_pool_size <= 0:
            return
        if self._tree is None or not self._wants_thread_pool(self._tree.root):
            return
        from bteng.concurrency.thread_pool import ThreadPool  # local: import cycle

        self._thread_pool = ThreadPool(num_threads=self._config.thread_pool_size)
        self._owns_thread_pool = True
        self._inject_thread_pool(self._tree.root, self._thread_pool)

    def _wants_thread_pool(self, node: TreeNode) -> bool:
        if getattr(node, "wants_thread_pool", False):
            return True
        return any(self._wants_thread_pool(child) for child in node.get_children())

    def _inject_thread_pool(self, node: TreeNode, pool: Any) -> None:
        if hasattr(node, "set_thread_pool"):
            node.set_thread_pool(pool)
        for child in node.get_children():
            self._inject_thread_pool(child, pool)

    def _wire_logger(self) -> None:
        """Subscribe logger to inspector so every node tick is logged automatically."""
        if self._inspector is None or self._logger is None:
            return
        # Remove previous subscription if logger or inspector changed
        if self._logger_sub_id is not None:
            self._inspector.unsubscribe(self._logger_sub_id)
            self._logger_sub_id = None

        logger = self._logger

        def _log_record(record: Any) -> None:
            logger.log_transition(
                record.uid, record.name,
                record.old_status, record.status,
                record.duration, record.feedback_message,
            )

        self._logger_sub_id = self._inspector.subscribe(_log_record)

    def __repr__(self) -> str:
        return (
            f"TreeExecutor(running={self.is_running()}, "
            f"ticks={self._tick_count}, tree={self._tree!r})"
        )
