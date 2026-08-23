"""Bridge between the synchronous tick loop and an asyncio event loop.

The behaviour tree keeps its synchronous tick model.  Coroutine-based leaf
nodes hand their work to an event loop through this bridge, which exposes the
same ``submit()`` contract as :class:`~bteng.concurrency.ThreadPool` — it
returns a :class:`concurrent.futures.Future` that
:class:`~bteng.nodes.leaf.AsyncActionNode` already polls each tick.

Two modes:

* **attached** — wrap a loop that somebody else owns (an OPC UA server, an
  aiohttp app, ...).  Nothing is started or stopped by the bridge::

      bridge = AsyncioBridge(asyncio.get_running_loop())

* **owned** — no loop given, so the bridge runs one on a daemon thread and
  stops it on :meth:`shutdown`::

      bridge = AsyncioBridge()
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, Optional, TypeVar

_T = TypeVar("_T")


class _TaskHolder:
    """Carries the asyncio Task backing a submitted coroutine.

    ``cancelled`` covers the window between submit() and the task actually
    starting, where there is no Task to cancel yet.
    """

    __slots__ = ("task", "cancelled", "settled")

    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self.cancelled = False
        self.settled = False


class AsyncioBridge:
    """Submits coroutines to an event loop, ThreadPool-style.

    Usage::

        bridge = AsyncioBridge(asyncio.get_running_loop())
        fut = bridge.submit(my_coroutine_function, arg)
        # later in tick():
        if fut.done():
            return fut.result()
    """

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._pending_count = 0
        self._pending_cond  = threading.Condition(threading.Lock())
        self._submit_lock   = threading.Lock()
        self._stopped       = False
        self._loop_error: Optional[BaseException] = None

        if loop is not None:
            self._loop   = loop
            self._owned  = False
            self._thread: Optional[threading.Thread] = None
            return

        self._loop  = asyncio.new_event_loop()
        self._owned = True
        ready = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.call_soon(ready.set)
            try:
                self._loop.run_forever()
            except BaseException as exc:  # SystemExit/KeyboardInterrupt escape Task.__step
                self._loop_error = exc
            finally:
                ready.set()

        self._thread = threading.Thread(
            target=_run, daemon=True, name="bteng-asyncio"
        )
        self._thread.start()
        ready.wait(timeout=5.0)

    @classmethod
    def from_running_loop(cls) -> "AsyncioBridge":
        """Attach to the loop running in the current thread."""
        return cls(asyncio.get_running_loop())

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    @property
    def owns_loop(self) -> bool:
        return self._owned

    @property
    def loop_error(self) -> Optional[BaseException]:
        """Exception that tore down an owned loop thread, if any."""
        return self._loop_error

    def is_alive(self) -> bool:
        """True while the bridge can still run coroutines."""
        return self._unusable_reason() is None

    def _unusable_reason(self) -> Optional[str]:
        if self._stopped:
            return "bridge has been shut down"
        if self._loop.is_closed():
            return "event loop is closed"
        if self._loop_error is not None:
            return f"event loop thread died: {self._loop_error!r}"
        if self._owned:
            if self._thread is None or not self._thread.is_alive():
                return "event loop thread is not running"
        elif not self._loop.is_running():
            # Attached loop the host has stopped (or not started). Queuing here
            # would hang every node forever, so refuse loudly instead.
            return "attached event loop is not running"
        return None

    def task_done(self, future: "Future[Any]") -> bool:
        """True if the coroutine behind ``future`` has actually finished.

        The concurrent Future can resolve as cancelled while the coroutine is
        still unwinding (or is suppressing the cancellation), so this reports
        the underlying Task instead.
        """
        holder = getattr(future, "bteng_task", None)
        if holder is None:
            return future.done()
        if holder.task is None:
            return holder.cancelled or future.done()
        return holder.task.done()

    def submit(
        self, fn: Callable[..., Awaitable[_T]], *args: Any, **kwargs: Any
    ) -> "Future[_T]":
        """Schedule ``fn(*args, **kwargs)`` on the loop.

        ``fn`` must return an awaitable — it is called on the *calling* thread
        so that a plain ``async def`` produces its coroutine here, then the
        coroutine itself is executed by the loop.

        Raises RuntimeError if the bridge can no longer run coroutines (shut
        down, loop closed, or the loop thread died).  An attached loop that the
        host has merely *stopped* cannot be detected — a submit then queues
        until the host runs it again.
        """
        reason = self._unusable_reason()
        if reason is not None:
            raise RuntimeError(f"AsyncioBridge: submit rejected — {reason}")

        coro = fn(*args, **kwargs)
        if not asyncio.iscoroutine(coro):
            raise TypeError(
                f"AsyncioBridge.submit: {getattr(fn, '__qualname__', fn)} did not "
                f"return a coroutine (got {type(coro).__name__}). "
                f"Use ThreadPool for blocking callables."
            )

        holder = _TaskHolder()

        async def _runner() -> _T:
            if holder.cancelled:            # cancelled before the task started
                coro.close()
                raise asyncio.CancelledError
            holder.task = asyncio.current_task()
            return await coro

        runner = _runner()

        # Hold _submit_lock across the liveness re-check and the scheduling call
        # so shutdown() cannot drain between them and strand this future.
        with self._submit_lock:
            reason = self._unusable_reason()
            if reason is None:
                with self._pending_cond:
                    self._pending_count += 1
                try:
                    future = asyncio.run_coroutine_threadsafe(runner, self._loop)
                except BaseException:
                    with self._pending_cond:
                        self._pending_count -= 1
                        self._pending_cond.notify_all()
                    reason = "event loop rejected the coroutine"
            if reason is not None:
                # Close both coroutines so neither surfaces as "never awaited".
                runner.close()
                coro.close()
                raise RuntimeError(f"AsyncioBridge: submit rejected — {reason}")

        # Used by cancel_task(); cancelling the Task (not the concurrent Future)
        # lets the coroutine unwind before the Future resolves.
        future.bteng_task = holder  # type: ignore[attr-defined]

        def _done(_f: "Future[_T]") -> None:
            if holder.task is None:
                # Task was cancelled (or the loop torn down) before it ever ran,
                # so `await coro` never happened — close it explicitly.
                coro.close()
            self._settle(holder)

        future.add_done_callback(_done)
        return future

    def cancel_task(self, future: "Future[Any]") -> bool:
        """Hard-cancel the coroutine behind ``future`` (raises CancelledError in it).

        Returns False if the future did not come from this bridge or the task
        has not started yet.  The future resolves once the coroutine unwinds,
        so callers can still wait on it.
        """
        holder = getattr(future, "bteng_task", None)
        if holder is None:
            return False
        # Covers the pre-start window: _runner checks this flag before awaiting.
        holder.cancelled = True
        task = holder.task
        if task is None:
            return True
        try:
            self._loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # loop already closed
            return False
        return True

    def pending_tasks(self) -> int:
        with self._pending_cond:
            return self._pending_count

    def _settle(self, holder: _TaskHolder) -> None:
        """Decrement the pending counter exactly once for a submission."""
        with self._pending_cond:
            if holder.settled:
                return
            holder.settled = True
            self._pending_count -= 1
            self._pending_cond.notify_all()

    def discard(self, future: "Future[Any]") -> bool:
        """Stop accounting for a future that can never resolve (dead loop).

        Without this the pending counter would stay high forever and
        :meth:`wait_all` would block until its timeout.
        """
        holder = getattr(future, "bteng_task", None)
        if holder is None:
            return False
        self._settle(holder)
        return True

    def in_loop_thread(self) -> bool:
        """True when the caller is running on this bridge's event loop."""
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    _LOOP_THREAD_HINT = ("Drive the tick loop from another thread "
                         "(asyncio.to_thread).")

    def _assert_not_loop_thread(self, what: str, hint: Optional[str] = None) -> None:
        if self.in_loop_thread():
            raise RuntimeError(
                f"AsyncioBridge: {what} called from the event loop thread — "
                f"it would block the loop that must complete the work. "
                f"{hint or self._LOOP_THREAD_HINT}"
            )

    def wait_all(self, timeout: float = 30.0) -> bool:
        """Block until all currently-submitted coroutines complete.

        Non-destructive — the bridge stays usable.  Returns False on timeout.
        Raises RuntimeError if called from the loop thread, where it would
        deadlock.

        ``timeout <= 0`` is a non-blocking poll: it returns immediately, True
        only if nothing is pending right now.  (It used to mean "wait forever",
        which no caller can have wanted from ``wait_all(0)``.)
        """
        self._assert_not_loop_thread("wait_all()")
        if timeout <= 0:
            with self._pending_cond:
                return self._pending_count == 0
        deadline = time.monotonic() + timeout
        with self._pending_cond:
            while self._pending_count > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._pending_cond.wait(timeout=remaining)
            return True

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop accepting work; stop the loop only if this bridge owns it.

        An owned loop is drained first: every in-flight coroutine is cancelled
        and awaited, so ``finally`` blocks run instead of the tasks being
        destroyed pending.  An attached loop belongs to the host, so its
        in-flight coroutines are left to finish on their own.

        Tearing down an *owned* loop from the loop thread itself is impossible —
        the drain has to be awaited by this very thread and the loop thread
        cannot join itself — so it raises RuntimeError immediately instead of
        stalling for ``timeout`` and then failing with "cannot join current
        thread".  Nothing is mutated in that case, so a later call from another
        thread (``threading.Thread(target=bridge.shutdown).start()``) still
        drains and closes the loop properly.  Shutting down an *attached*
        bridge from the loop thread is fine: it only stops accepting work.
        """
        if self._owned and not self._loop.is_closed():
            # Checked before _stopped is set: a rejected shutdown must leave the
            # bridge exactly as it was, or the retry would early-return at the
            # _stopped guard and the loop could never be closed at all.
            self._assert_not_loop_thread(
                "shutdown()",
                "The loop thread cannot join itself. Shut the bridge down from "
                "another thread, e.g. threading.Thread("
                "target=bridge.shutdown).start().",
            )

        # Take the submit lock so no submit is mid-flight while we drain.
        with self._submit_lock:
            if self._stopped:
                return
            self._stopped = True
        if not self._owned or self._loop.is_closed():
            return

        async def _drain() -> None:
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_drain(), self._loop).result(timeout=timeout)
        except BaseException:
            pass  # best effort — a wedged coroutine must not block shutdown

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if not self._loop.is_closed():
            self._loop.close()

    def __repr__(self) -> str:
        mode = "owned" if self._owned else "attached"
        state = "alive" if self.is_alive() else (self._unusable_reason() or "dead")
        return (f"AsyncioBridge({mode}, pending={self.pending_tasks()}, "
                f"state={state!r})")


_default_bridge: Optional[AsyncioBridge] = None
_default_lock = threading.Lock()


def set_default_bridge(bridge: Optional[AsyncioBridge]) -> None:
    """Set the bridge used by coroutine nodes that were given none.

    Call this once at startup with the host application's loop::

        set_default_bridge(AsyncioBridge(asyncio.get_running_loop()))
    """
    global _default_bridge
    with _default_lock:
        _default_bridge = bridge


def get_default_bridge() -> AsyncioBridge:
    """Return the default bridge, creating an owned-loop one on first use."""
    global _default_bridge
    with _default_lock:
        if _default_bridge is None:
            _default_bridge = AsyncioBridge()
        return _default_bridge


def shutdown_default_bridge() -> None:
    """Stop and clear the default bridge (no-op if never created).

    Raises RuntimeError if called from inside a coroutine running on the
    default bridge's own loop — see :meth:`AsyncioBridge.shutdown`.  The bridge
    is left intact in that case, so a call from another thread still works.
    """
    global _default_bridge
    with _default_lock:
        if _default_bridge is not None:
            _default_bridge.shutdown()
            _default_bridge = None
