"""Coroutine action node — runs ``async def`` work on an asyncio event loop.

The tick loop stays synchronous.  The coroutine is scheduled on an
:class:`~bteng.concurrency.AsyncioBridge`, and the node reports RUNNING until
the resulting Future is done — the same polling path
:class:`~bteng.nodes.leaf.AsyncActionNode` uses for thread work.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Awaitable, Callable, Optional

from bteng.concurrency.asyncio_bridge import AsyncioBridge, get_default_bridge
from bteng.concurrency.cancellation_token import CancellationToken
from bteng.core.node import NodeConfig, NodeStatus
from bteng.nodes.leaf.async_action import AsyncActionNode

logger = logging.getLogger(__name__)


class CoroActionNode(AsyncActionNode):
    """Runs ``execute_async()`` as a coroutine on an event loop.

    Override :meth:`execute_async` with ``async def``.  Await host-application
    coroutines freely; poll ``token.is_cancelled()`` between awaits so that
    halting the tree can stop the work.

    The loop is resolved in this order: a bridge set with :meth:`set_bridge`,
    else the default bridge (:func:`~bteng.concurrency.set_default_bridge`),
    else a lazily created owned loop on a daemon thread.  The shared
    ``ThreadPool`` injected by the executor is ignored — coroutines belong on
    the loop, not on worker threads.

    Example::

        class ReadTag(CoroActionNode):
            async def execute_async(self, token: CancellationToken) -> NodeStatus:
                value = await client.read(self.name)
                self.blackboard.set("value", value)
                return NodeStatus.SUCCESS
    """

    #: coroutines run on the loop, so this node never needs the shared ThreadPool
    wants_thread_pool = False

    def __init__(self, name: str, config: Optional[NodeConfig] = None) -> None:
        super().__init__(name, config)
        self._bridge: Optional[AsyncioBridge] = None
        self._orphan: Optional[Future] = None

    def set_bridge(self, bridge: AsyncioBridge) -> None:
        """Pin this node to a specific bridge (overrides the default)."""
        self._bridge = bridge
        self._thread_pool = bridge

    def set_thread_pool(self, pool: Any) -> None:
        """Ignore the executor's ThreadPool — coroutines run on the loop."""
        return

    def _ensure_bridge(self) -> None:
        """Resolve a live bridge, re-resolving if the cached one died.

        A node pinned with :meth:`set_bridge` never silently migrates to another
        loop — running host I/O on the wrong loop is worse than failing.
        """
        if self._bridge is not None:
            if not self._bridge.is_alive():
                raise RuntimeError(
                    f"AsyncioBridge: {self.name!r} is pinned to an unusable bridge "
                    f"({self._bridge._unusable_reason()})"
                )
            self._thread_pool = self._bridge
            return

        bridge = self._thread_pool
        if bridge is not None and getattr(bridge, "is_alive", lambda: True)():
            return
        self._thread_pool = get_default_bridge()

    def tick(self) -> NodeStatus:
        if self._orphan is not None:
            # A previous halt left work still unwinding — never run two copies.
            if not self._coro_finished(self._orphan):
                self.set_feedback_message("previous coroutine still unwinding")
                return NodeStatus.FAILURE
            self._orphan = None

        # A loop that died under an in-flight coroutine leaves a Future that can
        # never resolve — fail instead of ticking RUNNING forever.
        bridge = self._thread_pool
        if (self._future is not None and not self._future.done()
                and bridge is not None and hasattr(bridge, "is_alive")
                and not bridge.is_alive()):
            if hasattr(bridge, "discard"):
                bridge.discard(self._future)   # the done-callback will never fire
            self._future = None
            self.set_feedback_message("event loop died while the coroutine was in flight")
            return NodeStatus.FAILURE

        try:
            self._ensure_bridge()
            return super().tick()
        except asyncio.CancelledError:
            # CancelledError is a BaseException — AsyncActionNode.tick() lets it through.
            return NodeStatus.FAILURE
        except RuntimeError as exc:
            # Bridge unusable (shut down, loop dead) — a status beats an
            # exception escaping the tick contract.
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

    #: seconds the coroutine gets to notice ``token.is_cancelled()`` before it
    #: is hard-cancelled with :exc:`asyncio.CancelledError`.  The halting thread
    #: blocks for this long, so keep it short — a coroutine that polls the token
    #: never reaches it.  Raise it per class only when cleanup genuinely needs
    #: more time.
    HALT_GRACE = 0.2

    def _coro_finished(self, future: Future) -> bool:
        """Has the coroutine itself finished (not just the concurrent Future)?"""
        bridge = self._thread_pool
        if bridge is not None and hasattr(bridge, "task_done"):
            return bridge.task_done(future)
        return future.done()

    def _on_halt(self) -> None:
        bridge = self._thread_pool
        self._cancel_token.cancel()
        future = self._future

        # On the loop thread (a tree ticked directly from the loop, e.g. a
        # Timeout decorator halting its child) we must not block on the Future —
        # the loop that has to complete it is this very thread. Cancel and let
        # the coroutine unwind in the background; the orphan guard stops a second
        # copy from starting before it does.
        if future is not None and bridge is not None and getattr(
            bridge, "in_loop_thread", lambda: False
        )():
            if hasattr(bridge, "cancel_task"):
                bridge.cancel_task(future)
            if not self._coro_finished(future):
                self._orphan = future
            self._future = None
            with self._lock:
                self._result = None
            self._thread = None
            return

        if future is not None:
            try:
                future.result(timeout=self.HALT_GRACE)
            except FuturesTimeout:
                # Coroutine ignored the token — cancel the task and let it unwind.
                # This stalled the halting thread for the whole grace period, so
                # say so: a node that does this quietly eats ticks.
                logger.warning(
                    "CoroActionNode %r did not stop within HALT_GRACE (%.3gs) — "
                    "forcing asyncio cancellation. Poll token.is_cancelled() "
                    "between awaits to halt promptly.",
                    self.name,
                    self.HALT_GRACE,
                )
                if bridge is not None and hasattr(bridge, "cancel_task"):
                    bridge.cancel_task(future)
                try:
                    future.result(timeout=self.JOIN_TIMEOUT)
                except BaseException:
                    pass
            except BaseException:
                pass
            if not self._coro_finished(future):
                # Cleanup that itself awaits can outlive the Future resolving.
                # Keep the handle so the next tick refuses to start a 2nd copy.
                self._orphan = future
            self._future = None
        with self._lock:
            self._result = None
        self._thread = None

    async def execute_async(self, token: CancellationToken) -> NodeStatus:  # type: ignore[override]
        """Override with coroutine logic.  Runs on the event loop."""
        raise NotImplementedError(f"{type(self).__name__}.execute_async() not implemented")


class FunctionCoroAction(CoroActionNode):
    """Wraps a coroutine function as a :class:`CoroActionNode`.

    The coroutine receives ``self`` (the node) and the cancellation token, and
    returns a :class:`NodeStatus` or a bool (True → SUCCESS, False → FAILURE).
    """

    def __init__(
        self,
        name: str,
        fn: Callable[["FunctionCoroAction", CancellationToken], Awaitable[Any]],
        config: Optional[NodeConfig] = None,
    ) -> None:
        super().__init__(name, config)
        self._fn = fn

    async def execute_async(self, token: CancellationToken) -> NodeStatus:
        result = await self._fn(self, token)
        if isinstance(result, NodeStatus):
            return result
        return NodeStatus.SUCCESS if result else NodeStatus.FAILURE


def coro_action(
    name: str,
    fn: Callable[["FunctionCoroAction", CancellationToken], Awaitable[Any]],
) -> FunctionCoroAction:
    """Convenience factory for inline coroutine action nodes."""
    return FunctionCoroAction(name, fn)
