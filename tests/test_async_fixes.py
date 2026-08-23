"""Async leaf / concurrency fixes.

Four failure modes that all shared one shape — the caller was left waiting for
something that was never going to happen:

* ``execute_async()`` returning a non-NodeStatus (a missing ``return`` yields
  None) wedged the node at RUNNING forever, and ``except Exception`` let a
  BaseException do the same.  The thread path hung silently while the pool path
  raised TypeError from ``execute_tick()`` — two paths, two behaviours.
* halting a node whose pool future was still *queued* burned the full
  JOIN_TIMEOUT (5 s) on the tick thread waiting for work that had not started
  and, on a halt, never should.
* ``AsyncioBridge.shutdown()`` from the loop thread self-deadlocked, then raised
  "cannot join current thread", and left the loop unclosable.
* ``wait_all(timeout=0)`` blocked forever instead of polling.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from bteng.concurrency.asyncio_bridge import (
    AsyncioBridge, set_default_bridge, shutdown_default_bridge,
)
from bteng.concurrency.thread_pool import ThreadPool
from bteng.core.node import NodeStatus
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.leaf.async_action import AsyncActionNode


@pytest.fixture(autouse=True)
def _clean_default_bridge():
    yield
    shutdown_default_bridge()


@pytest.fixture
def pool():
    """A 1-thread pool, so 'running' vs 'queued' is fully deterministic."""
    p = ThreadPool(num_threads=1)
    try:
        yield p
    finally:
        p.shutdown()


def _settle(node, limit: int = 300, sleep: float = 0.01) -> NodeStatus:
    """Tick until the node reports something other than RUNNING."""
    for _ in range(limit):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(sleep)
    raise AssertionError(f"{node.name} never left RUNNING")


class _Boom(BaseException):
    """Not an Exception — the old ``except Exception`` missed it entirely."""


class _ForgotToReturn(AsyncActionNode):
    def execute_async(self, token):
        return None                      # user forgot `return NodeStatus.SUCCESS`


class _ReturnsBool(AsyncActionNode):
    def execute_async(self, token):
        return True                      # truthy, but still not a NodeStatus


class _RaisesBaseException(AsyncActionNode):
    def execute_async(self, token):
        raise _Boom("worker exploded")


class _Fine(AsyncActionNode):
    def execute_async(self, token):
        return NodeStatus.SUCCESS


# ─────────────────────────────────────────────────────────────────────────────
# F2 — a worker that never produced a NodeStatus left the node RUNNING forever
# ─────────────────────────────────────────────────────────────────────────────

class TestNonStatusResultSettles:

    def test_none_on_the_thread_path_settles_as_failure(self):
        node = _ForgotToReturn("forgot")
        assert _settle(node) == NodeStatus.FAILURE
        assert "_ForgotToReturn" in node.feedback_message
        assert "NoneType" in node.feedback_message

    def test_none_on_the_pool_path_settles_as_failure(self, pool):
        node = _ForgotToReturn("forgot_pool")
        node.set_thread_pool(pool)
        # Used to raise TypeError out of execute_tick() while the thread path
        # hung — the two paths must agree.
        assert _settle(node) == NodeStatus.FAILURE
        assert "_ForgotToReturn" in node.feedback_message
        assert "NoneType" in node.feedback_message

    def test_thread_and_pool_paths_agree(self, pool):
        threaded = _ForgotToReturn("threaded")
        pooled = _ForgotToReturn("pooled")
        pooled.set_thread_pool(pool)
        assert _settle(threaded) == _settle(pooled) == NodeStatus.FAILURE
        assert threaded.feedback_message == pooled.feedback_message

    @pytest.mark.parametrize("use_pool", [False, True])
    def test_truthy_non_status_is_not_treated_as_success(self, use_pool, pool):
        node = _ReturnsBool("boolish")
        if use_pool:
            node.set_thread_pool(pool)
        assert _settle(node) == NodeStatus.FAILURE
        assert "bool" in node.feedback_message

    @pytest.mark.parametrize("use_pool", [False, True])
    def test_base_exception_settles_instead_of_hanging(self, use_pool, pool):
        node = _RaisesBaseException("boom")
        if use_pool:
            node.set_thread_pool(pool)
        assert _settle(node) == NodeStatus.FAILURE
        assert "_Boom" in node.feedback_message
        assert "worker exploded" in node.feedback_message

    @pytest.mark.parametrize("use_pool", [False, True])
    def test_a_well_behaved_node_is_untouched(self, use_pool, pool):
        node = _Fine("fine")
        if use_pool:
            node.set_thread_pool(pool)
        assert _settle(node) == NodeStatus.SUCCESS
        assert node.feedback_message == ""

    def test_the_tree_advances_past_a_forgetful_leaf(self, pool):
        # The point of the fix: a parent used to sit on RUNNING forever.
        leaf = _ForgotToReturn("leaf")
        leaf.set_thread_pool(pool)
        seq = SequenceNode("seq", [leaf])
        assert _settle(seq) == NodeStatus.FAILURE

    def test_worker_thread_is_dead_by_the_time_the_node_settles(self):
        node = _ForgotToReturn("forgot")
        node.execute_tick()
        thread = node._thread
        assert thread is not None
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        # The worker finished long ago; the very next tick must not say RUNNING.
        assert node.execute_tick() == NodeStatus.FAILURE


# ─────────────────────────────────────────────────────────────────────────────
# F4 — halting a queued pool future waited JOIN_TIMEOUT for nothing
# ─────────────────────────────────────────────────────────────────────────────

class _Blocking(AsyncActionNode):
    """Ignores the token, like third-party blocking I/O."""

    gate = None                          # class attr set per test

    def execute_async(self, token):
        type(self).gate.wait(10.0)
        return NodeStatus.SUCCESS


class TestHaltCancelsQueuedWork:

    @staticmethod
    def _blocking_class(gate):
        return type("_Gated", (_Blocking,), {"gate": gate})

    def test_halting_a_queued_future_returns_immediately(self, pool):
        gate = threading.Event()
        cls = self._blocking_class(gate)
        running, queued = cls("running"), cls("queued")
        running.set_thread_pool(pool)
        queued.set_thread_pool(pool)
        try:
            running.execute_tick()
            queued.execute_tick()
            time.sleep(0.05)
            assert not queued._future.running(), "test needs a genuinely queued future"

            t0 = time.monotonic()
            queued.halt()
            elapsed = time.monotonic() - t0

            # Was exactly JOIN_TIMEOUT (5.00 s) before the fix.
            assert elapsed < 0.5, f"halt of a queued future took {elapsed:.2f}s"
            assert queued._future is None
            assert queued.status == NodeStatus.IDLE
        finally:
            gate.set()
            running._cancel_token.cancel()

    def test_halting_many_queued_futures_stays_cheap(self, pool):
        # A Parallel wider than the pool: this used to cost 5 s per queued
        # child, serially, on the tick thread.
        gate = threading.Event()
        cls = self._blocking_class(gate)
        nodes = [cls(f"n{i}") for i in range(5)]
        for node in nodes:
            node.set_thread_pool(pool)
        try:
            for node in nodes:
                node.execute_tick()
            time.sleep(0.05)
            queued = nodes[1:]
            assert all(not n._future.running() for n in queued)

            t0 = time.monotonic()
            for node in queued:
                node.halt()
            elapsed = time.monotonic() - t0

            assert elapsed < 1.0, f"halting 4 queued children took {elapsed:.2f}s"
        finally:
            gate.set()
            for node in nodes:
                node._cancel_token.cancel()

    def test_halting_a_running_future_still_uses_the_timed_join(self, pool):
        gate = threading.Event()
        started = threading.Event()

        class _Deaf(AsyncActionNode):
            JOIN_TIMEOUT = 0.3

            def execute_async(self, token):
                started.set()
                gate.wait(10.0)          # never notices the token
                return NodeStatus.SUCCESS

        node = _Deaf("deaf")
        node.set_thread_pool(pool)
        try:
            node.execute_tick()
            assert started.wait(2.0), "worker never started"
            assert node._future.running()

            t0 = time.monotonic()
            node.halt()
            elapsed = time.monotonic() - t0

            # cancel() refuses a started future, so the timed join still runs —
            # it must wait JOIN_TIMEOUT and then give up, not wait forever.
            assert elapsed >= 0.2, f"the timed join was skipped ({elapsed:.2f}s)"
            assert elapsed < 2.0, f"halt overran the timed join ({elapsed:.2f}s)"
            assert node._future is None
        finally:
            gate.set()

    def test_halting_a_queued_future_does_not_leak_the_pool_accounting(self, pool):
        # A cancelled future never runs the pool's wrapper, so the pending
        # counter has to be settled by the done-callback or wait_all() hangs.
        gate = threading.Event()
        cls = self._blocking_class(gate)
        running, queued = cls("running"), cls("queued")
        running.set_thread_pool(pool)
        queued.set_thread_pool(pool)
        try:
            running.execute_tick()
            queued.execute_tick()
            time.sleep(0.05)
            assert pool.pending_tasks() == 2
            queued.halt()
            assert pool.pending_tasks() == 1
        finally:
            gate.set()
            running._cancel_token.cancel()
        assert pool.wait_all(timeout=5.0)
        assert pool.pending_tasks() == 0

    def test_a_halted_node_can_run_again(self, pool):
        gate = threading.Event()
        cls = self._blocking_class(gate)
        running, queued = cls("running"), cls("queued")
        running.set_thread_pool(pool)
        queued.set_thread_pool(pool)
        try:
            running.execute_tick()
            queued.execute_tick()
            time.sleep(0.05)
            queued.halt()
        finally:
            gate.set()
            running._cancel_token.cancel()
        assert pool.wait_all(timeout=5.0)
        assert _settle(queued) == NodeStatus.SUCCESS   # cancel token was reset


class TestThreadPoolCancelAccounting:

    def test_cancelled_queued_task_settles_the_pending_count(self, pool):
        gate = threading.Event()
        pool.submit(gate.wait, 10.0)
        time.sleep(0.05)
        queued = pool.submit(gate.wait, 10.0)
        assert pool.pending_tasks() == 2
        assert queued.cancel()
        assert pool.pending_tasks() == 1
        gate.set()
        assert pool.wait_all(timeout=5.0)
        assert pool.pending_tasks() == 0

    def test_normal_completion_decrements_exactly_once(self, pool):
        futs = [pool.submit(lambda: 1) for _ in range(5)]
        assert pool.wait_all(timeout=5.0)
        assert [f.result() for f in futs] == [1] * 5
        assert pool.pending_tasks() == 0


# ─────────────────────────────────────────────────────────────────────────────
# F13 — shutdown() from the loop thread deadlocked, raised, and leaked the loop
# ─────────────────────────────────────────────────────────────────────────────

class TestShutdownFromLoopThread:

    def test_owned_shutdown_from_the_loop_thread_fails_fast(self):
        bridge = AsyncioBridge()
        observed = {}

        async def _probe():
            t0 = time.monotonic()
            try:
                bridge.shutdown(timeout=1.0)
            except BaseException as exc:      # noqa: BLE001 — record whatever it is
                observed["exc"] = f"{type(exc).__name__}: {exc}"
            observed["elapsed"] = time.monotonic() - t0

        try:
            bridge.submit(_probe).result(timeout=10.0)
            assert "RuntimeError" in observed.get("exc", "")
            assert "shutdown() called from the event loop thread" in observed["exc"]
            # Used to stall for the whole drain timeout and then raise
            # "cannot join current thread".
            assert observed["elapsed"] < 0.5
            assert "cannot join current thread" not in observed["exc"]
        finally:
            bridge.shutdown()

    def test_a_refused_shutdown_leaves_the_bridge_untouched(self):
        bridge = AsyncioBridge()

        async def _probe():
            try:
                bridge.shutdown(timeout=1.0)
            except RuntimeError:
                pass

        bridge.submit(_probe).result(timeout=10.0)
        # _stopped must NOT have been set, or the retry below early-returns and
        # the loop can never be closed.
        assert bridge.is_alive()
        assert not bridge.loop.is_closed()
        assert bridge.submit(asyncio.sleep, 0.0, 7).result(timeout=5.0) == 7

    def test_shutdown_from_another_thread_still_closes_the_loop(self):
        bridge = AsyncioBridge()

        async def _probe():
            try:
                bridge.shutdown(timeout=1.0)
            except RuntimeError:
                pass

        bridge.submit(_probe).result(timeout=10.0)

        thread = threading.Thread(target=bridge.shutdown, daemon=True)
        thread.start()
        thread.join(timeout=10.0)
        assert not thread.is_alive(), "the retry from another thread hung"
        assert bridge.loop.is_closed()
        assert bridge._thread is None
        assert not bridge.is_alive()

    def test_attached_shutdown_from_the_loop_thread_is_allowed(self):
        # Nothing to join or close for an attached loop — refusing here would be
        # a regression for hosts that tear down from inside a coroutine.
        holder = {}

        async def main():
            bridge = AsyncioBridge.from_running_loop()
            bridge.shutdown()
            holder["bridge"] = bridge

        asyncio.run(main())
        assert not holder["bridge"].is_alive()

    def test_shutdown_default_bridge_from_a_coroutine_is_recoverable(self):
        bridge = AsyncioBridge()
        set_default_bridge(bridge)
        observed = {}

        async def _teardown():
            try:
                shutdown_default_bridge()
            except RuntimeError as exc:
                observed["exc"] = str(exc)

        bridge.submit(_teardown).result(timeout=10.0)
        assert "event loop thread" in observed.get("exc", "")
        assert bridge.is_alive()

        thread = threading.Thread(target=shutdown_default_bridge, daemon=True)
        thread.start()
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert bridge.loop.is_closed()

    def test_no_pending_task_is_destroyed_by_the_refused_shutdown(self):
        bridge = AsyncioBridge()
        try:
            async def _probe():
                try:
                    bridge.shutdown(timeout=1.0)
                except RuntimeError:
                    pass
                return "ok"

            assert bridge.submit(_probe).result(timeout=10.0) == "ok"
            assert bridge.pending_tasks() == 0
        finally:
            bridge.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# F14 — wait_all(timeout=0) blocked forever instead of polling
# ─────────────────────────────────────────────────────────────────────────────

class TestWaitAllNonBlockingPoll:

    @pytest.mark.parametrize("timeout", [0, 0.0, -1.0])
    def test_bridge_poll_returns_immediately_while_work_is_pending(self, timeout):
        bridge = AsyncioBridge()
        try:
            bridge.submit(asyncio.sleep, 2.0)
            t0 = time.monotonic()
            result = bridge.wait_all(timeout=timeout)
            elapsed = time.monotonic() - t0
            assert result is False
            assert elapsed < 0.5, f"wait_all({timeout}) blocked for {elapsed:.2f}s"
        finally:
            bridge.shutdown()

    def test_bridge_poll_returns_true_when_idle(self):
        bridge = AsyncioBridge()
        try:
            bridge.submit(asyncio.sleep, 0.0)
            assert bridge.wait_all(timeout=5.0)
            assert bridge.wait_all(timeout=0) is True
        finally:
            bridge.shutdown()

    def test_bridge_poll_still_refuses_the_loop_thread(self):
        bridge = AsyncioBridge()
        observed = {}

        async def _probe():
            try:
                bridge.wait_all(timeout=0)
            except RuntimeError as exc:
                observed["exc"] = str(exc)

        try:
            bridge.submit(_probe).result(timeout=10.0)
            assert "event loop thread" in observed.get("exc", "")
        finally:
            bridge.shutdown()

    @pytest.mark.parametrize("timeout", [0, 0.0, -1.0])
    def test_pool_poll_returns_immediately_while_work_is_pending(self, pool, timeout):
        gate = threading.Event()
        try:
            pool.submit(gate.wait, 10.0)
            time.sleep(0.05)
            t0 = time.monotonic()
            result = pool.wait_all(timeout=timeout)
            elapsed = time.monotonic() - t0
            assert result is False
            assert elapsed < 0.5, f"wait_all({timeout}) blocked for {elapsed:.2f}s"
        finally:
            gate.set()

    def test_pool_poll_returns_true_when_idle(self, pool):
        pool.submit(lambda: 1)
        assert pool.wait_all(timeout=5.0)
        assert pool.wait_all(timeout=0) is True

    def test_positive_timeout_still_waits(self, pool):
        pool.submit(time.sleep, 0.2)
        t0 = time.monotonic()
        assert pool.wait_all(timeout=5.0)
        assert time.monotonic() - t0 >= 0.15
