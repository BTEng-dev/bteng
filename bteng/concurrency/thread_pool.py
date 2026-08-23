"""Fixed-size worker thread pool for async node execution."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


class ThreadPool:
    """Fixed-size worker thread pool.

    AsyncActionNode submits work via submit() and polls the returned
    Future each tick until it is ready.  Threads stay alive for the
    lifetime of the executor — no thread-creation overhead during execution.

    Usage::

        pool = ThreadPool(num_threads=4)
        fut = pool.submit(lambda: NodeStatus.SUCCESS)
        # later in tick():
        if fut.done():
            return fut.result()
        # wait for all current tasks (non-destructive):
        pool.wait_all()
    """

    def __init__(self, num_threads: int = 4) -> None:
        if num_threads <= 0:
            num_threads = os.cpu_count() or 4
        self._num_threads = num_threads
        self._executor = ThreadPoolExecutor(
            max_workers=num_threads, thread_name_prefix="bteng-worker"
        )
        self._stopped = False
        self._pending_count = 0
        self._pending_cond  = threading.Condition(threading.Lock())

    def submit(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> "Future[_T]":
        """Submit a callable for async execution.

        Returns a Future that becomes ready when a worker finishes the task.
        Raises RuntimeError if the pool has been stopped.
        """
        if self._stopped:
            raise RuntimeError("ThreadPool: submit on stopped pool")
        with self._pending_cond:
            self._pending_count += 1

        settled = [False]

        def _settle() -> None:
            with self._pending_cond:
                if settled[0]:
                    return
                settled[0] = True
                self._pending_count -= 1
                self._pending_cond.notify_all()

        def _wrapper(*a: Any, **kw: Any) -> _T:
            try:
                return fn(*a, **kw)
            finally:
                _settle()

        future = self._executor.submit(_wrapper, *args, **kwargs)
        # A future cancelled while still queued never runs _wrapper, so without
        # this the pending count would never come back down and wait_all() would
        # block until its timeout.  _settle() is idempotent, so the normal path
        # (wrapper first, callback second) still decrements exactly once.
        future.add_done_callback(lambda _f: _settle())
        return future

    @property
    def thread_count(self) -> int:
        return self._num_threads

    def pending_tasks(self) -> int:
        with self._pending_cond:
            return self._pending_count

    def wait_all(self, timeout: float = 30.0) -> bool:
        """Block until all currently-submitted tasks complete.

        Non-destructive — the pool remains usable after this call.
        Returns True if all tasks completed, False if timeout expired.

        ``timeout <= 0`` is a non-blocking poll: it returns immediately, True
        only if nothing is pending right now.  (It used to mean "wait forever",
        which no caller can have wanted from ``wait_all(0)``.)

        Unlike shutdown(), this does NOT stop the executor; new tasks can
        be submitted after wait_all() returns.
        """
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

    def shutdown(self) -> None:
        """Stop the pool and wait for running tasks to finish."""
        self._stopped = True
        self._executor.shutdown(wait=True)

    def __del__(self) -> None:
        if not self._stopped:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

    def __repr__(self) -> str:
        return f"ThreadPool(threads={self._num_threads}, stopped={self._stopped})"
