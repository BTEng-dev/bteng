"""Async action node — runs work in a background thread."""
from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import Any, Optional

from bteng.core.node import NodeConfig, NodeStatus
from bteng.concurrency.cancellation_token import CancellationToken
from bteng.nodes.leaf.action import ActionNode


class AsyncActionNode(ActionNode):
    """Runs execute_async() in a background thread, returns RUNNING while in-flight.

    Override :meth:`execute_async` with long-running logic.
    Check ``token.is_cancelled()`` periodically for cooperative cancellation.

    The node can use a shared :class:`~bteng.concurrency.ThreadPool` injected
    by the executor (set_thread_pool), or falls back to daemon threads.

    Example::

        class MyTask(AsyncActionNode):
            def execute_async(self, token: CancellationToken) -> NodeStatus:
                for i in range(10):
                    if token.is_cancelled():
                        return NodeStatus.FAILURE
                    time.sleep(0.1)
                return NodeStatus.SUCCESS
    """

    JOIN_TIMEOUT = 5.0  # seconds to wait when halting

    #: TreeExecutor builds its shared ThreadPool only if some node in the tree
    #: wants one.  Subclasses that run their work elsewhere set this False.
    wants_thread_pool = True

    def __init__(self, name: str, config: Optional[NodeConfig] = None) -> None:
        super().__init__(name, config)
        self._thread:       Optional[threading.Thread] = None
        self._future:       Optional[Future]           = None
        self._result:       Optional[NodeStatus]       = None
        self._result_feedback: Optional[str]           = None
        self._cancel_token: CancellationToken          = CancellationToken.create()
        self._lock          = threading.Lock()
        self._thread_pool   = None  # Optional[ThreadPool] — injected by executor

    def set_thread_pool(self, pool: Any) -> None:
        """Inject a shared ThreadPool (called by TreeExecutor before first tick)."""
        self._thread_pool = pool

    def tick(self) -> NodeStatus:
        if self._status != NodeStatus.RUNNING:
            # Start fresh
            with self._lock:
                self._result          = None
                self._result_feedback = None
            self._cancel_token.reset()

            if self._thread_pool is not None:
                self._future = self._thread_pool.submit(
                    self.execute_async, self._cancel_token
                )
                self._thread = None
            else:
                self._future = None
                self._thread = threading.Thread(
                    target=self._thread_body,
                    daemon=True,
                    name=f"bteng-{self.name}",
                )
                self._thread.start()
            return NodeStatus.RUNNING

        # Check for completion
        if self._future is not None:
            if self._future.done():
                try:
                    raw = self._future.result()
                except BaseException as exc:  # noqa: BLE001 — see _settle_exception
                    return self._settle_exception(exc)
                return self._settle_result(raw)
            return NodeStatus.RUNNING

        with self._lock:
            result   = self._result
            feedback = self._result_feedback
        if result is not None:
            if feedback:
                self.set_feedback_message(feedback)
            return result
        return NodeStatus.RUNNING

    # ── result handling ──────────────────────────────────────────────────────
    #
    # The thread path and the pool path must agree.  Whatever execute_async()
    # produces — a status, a stray None, or a BaseException — the node has to
    # *settle*: a leaf that stays RUNNING after its worker died never lets the
    # tree advance, and nothing is logged to say why.

    def _bad_result_message(self, raw: Any) -> str:
        return (
            f"{type(self).__name__}.execute_async() returned "
            f"{type(raw).__name__} ({raw!r}), expected NodeStatus — "
            f"treating as FAILURE"
        )

    def _exception_message(self, exc: BaseException) -> str:
        return (
            f"{type(self).__name__}.execute_async() raised "
            f"{type(exc).__name__}: {exc}"
        )

    def _settle_result(self, raw: Any) -> NodeStatus:
        """Turn execute_async()'s return value into a NodeStatus.

        A missing ``return`` yields None, which used to wedge the node at
        RUNNING forever.  Anything that is not a NodeStatus becomes FAILURE
        with a feedback message naming the offending class.
        """
        if isinstance(raw, NodeStatus):
            return raw
        self.set_feedback_message(self._bad_result_message(raw))
        return NodeStatus.FAILURE

    def _settle_exception(self, exc: BaseException) -> NodeStatus:
        """Turn an escaped exception into FAILURE.

        Deliberately catches BaseException: a CancelledError or SystemExit out
        of the worker used to leave the node RUNNING forever, which is a far
        worse failure mode than reporting FAILURE for it.
        """
        self.set_feedback_message(self._exception_message(exc))
        return NodeStatus.FAILURE

    def _thread_body(self) -> None:
        try:
            raw = self.execute_async(self._cancel_token)
        except BaseException as exc:  # noqa: BLE001 — see _settle_exception
            result, feedback = NodeStatus.FAILURE, self._exception_message(exc)
        else:
            if isinstance(raw, NodeStatus):
                result, feedback = raw, None
            else:
                result, feedback = NodeStatus.FAILURE, self._bad_result_message(raw)
        # Publish the message alongside the status so tick() — i.e. the tick
        # thread — is the one that touches feedback_message.
        with self._lock:
            self._result          = result
            self._result_feedback = feedback

    def _on_halt(self) -> None:
        self._cancel_token.cancel()
        if self._future is not None:
            # Work still sitting in the pool queue has nobody polling the token,
            # so waiting JOIN_TIMEOUT for it to notice is JOIN_TIMEOUT wasted on
            # the tick thread.  cancel() resolves a queued future instantly and
            # returns False once the work has actually started — only then is
            # the timed join the right tool.
            if not self._future.cancel():
                try:
                    self._future.result(timeout=self.JOIN_TIMEOUT)
                except BaseException:
                    pass  # cancelled/failed work must not break the halt
            self._future = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.JOIN_TIMEOUT)
        with self._lock:
            self._result          = None
            self._result_feedback = None
        self._thread = None

    def execute_async(self, token: CancellationToken) -> NodeStatus:
        """Override with long-running logic.  Runs in a background thread.

        Poll ``token.is_cancelled()`` (or ``token.is_set()`` for compat) to
        support cooperative cancellation when the node is halted.

        Must return a :class:`NodeStatus`.  Anything else — including the
        ``None`` of a missing return path — settles the node as FAILURE with a
        feedback message naming the class, as does any exception (including
        BaseException) that escapes.  The node never stays RUNNING after the
        work has finished.
        """
        raise NotImplementedError(f"{type(self).__name__}.execute_async() not implemented")


# type hint placeholder — avoids circular import with ThreadPool
from typing import Any  # noqa: E402
