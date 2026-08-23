"""Tests for CoroActionNode / AsyncioBridge — coroutine leaves on a sync tick loop."""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
import warnings

import pytest

from bteng.concurrency.asyncio_bridge import (
    AsyncioBridge, get_default_bridge, set_default_bridge, shutdown_default_bridge,
)
from bteng.concurrency.cancellation_token import CancellationToken
from bteng.core.executor import ExecutorConfig, TreeExecutor
from bteng.core.node import NodeStatus
from bteng.core.tree import Tree, TreeMetadata
from bteng.nodes.leaf.coro_action import CoroActionNode, coro_action


def _make_tree(root):
    return Tree(TreeMetadata(id="test"), root)


def _tick_until_done(node, limit: int = 500, sleep: float = 0.01) -> NodeStatus:
    for _ in range(limit):
        status = node.tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(sleep)
    raise AssertionError("node never left RUNNING")


@pytest.fixture(autouse=True)
def _clean_default_bridge():
    yield
    shutdown_default_bridge()


# ─────────────────────────────────────────────────────────────────────────────
# AsyncioBridge
# ─────────────────────────────────────────────────────────────────────────────

class TestAsyncioBridge:

    def test_owned_loop_runs_coroutine(self):
        bridge = AsyncioBridge()
        try:
            assert bridge.owns_loop
            fut = bridge.submit(asyncio.sleep, 0.01, "done")
            assert fut.result(timeout=5.0) == "done"
        finally:
            bridge.shutdown()

    def test_attached_loop_is_not_stopped_by_shutdown(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            bridge = AsyncioBridge(loop)
            assert not bridge.owns_loop
            assert bridge.submit(asyncio.sleep, 0.0, 42).result(timeout=5.0) == 42
            bridge.shutdown()
            assert loop.is_running()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5.0)
            loop.close()

    def test_submit_rejects_non_coroutine(self):
        bridge = AsyncioBridge()
        try:
            with pytest.raises(TypeError):
                bridge.submit(lambda: 1)
        finally:
            bridge.shutdown()

    def test_submit_after_shutdown_raises(self):
        bridge = AsyncioBridge()
        bridge.shutdown()
        with pytest.raises(RuntimeError):
            bridge.submit(asyncio.sleep, 0.0)

    def test_wait_all_and_pending_count(self):
        bridge = AsyncioBridge()
        try:
            futs = [bridge.submit(asyncio.sleep, 0.05) for _ in range(3)]
            assert bridge.wait_all(timeout=5.0)
            assert bridge.pending_tasks() == 0
            assert all(f.done() for f in futs)
        finally:
            bridge.shutdown()

    def test_shutdown_drains_in_flight_coroutines(self):
        unwound = []

        async def _work():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                unwound.append(True)
                raise

        bridge = AsyncioBridge()
        for _ in range(5):
            bridge.submit(_work)
        time.sleep(0.05)
        bridge.shutdown()
        assert len(unwound) == 5          # cleanup ran, tasks not abandoned
        assert bridge.pending_tasks() == 0

    def test_shutdown_is_idempotent(self):
        bridge = AsyncioBridge()
        bridge.shutdown()
        bridge.shutdown()                 # must not raise on a closed loop

    def test_submit_on_closed_loop_leaves_no_dangling_coroutine(self):
        loop = asyncio.new_event_loop()
        loop.close()
        bridge = AsyncioBridge(loop)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError):
                bridge.submit(asyncio.sleep, 0.0)
            gc.collect()
        assert not [w for w in caught if "never awaited" in str(w.message)]
        assert bridge.pending_tasks() == 0

    def test_from_running_loop(self):
        result = {}

        async def main():
            bridge = AsyncioBridge.from_running_loop()
            assert bridge.loop is asyncio.get_running_loop()
            assert not bridge.owns_loop
            result["ok"] = True

        asyncio.run(main())
        assert result["ok"]


# ─────────────────────────────────────────────────────────────────────────────
# CoroActionNode
# ─────────────────────────────────────────────────────────────────────────────

class _Sleeper(CoroActionNode):
    def __init__(self, name, delay=0.02, status=NodeStatus.SUCCESS):
        super().__init__(name)
        self.delay = delay
        self.result_status = status
        self.started = 0

    async def execute_async(self, token: CancellationToken) -> NodeStatus:
        self.started += 1
        await asyncio.sleep(self.delay)
        return self.result_status


class TestCoroActionNode:

    def test_returns_running_then_success(self):
        bridge = AsyncioBridge()
        try:
            node = _Sleeper("n", delay=0.05)
            node.set_bridge(bridge)
            assert node.tick() == NodeStatus.RUNNING
            node._status = NodeStatus.RUNNING  # executor normally does this
            assert _tick_until_done(node) == NodeStatus.SUCCESS
            assert node.started == 1
        finally:
            bridge.shutdown()

    def test_failure_status_propagates(self):
        bridge = AsyncioBridge()
        try:
            node = _Sleeper("n", delay=0.0, status=NodeStatus.FAILURE)
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            assert _tick_until_done(node) == NodeStatus.FAILURE
        finally:
            bridge.shutdown()

    def test_exception_becomes_failure(self):
        class _Boom(CoroActionNode):
            async def execute_async(self, token):
                raise RuntimeError("boom")

        bridge = AsyncioBridge()
        try:
            node = _Boom("n")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            assert _tick_until_done(node) == NodeStatus.FAILURE
        finally:
            bridge.shutdown()

    def test_uses_default_bridge_when_unbound(self):
        bridge = AsyncioBridge()
        set_default_bridge(bridge)
        node = _Sleeper("n", delay=0.0)
        node.tick()
        node._status = NodeStatus.RUNNING
        assert _tick_until_done(node) == NodeStatus.SUCCESS

    def test_lazily_creates_default_bridge(self):
        node = _Sleeper("n", delay=0.0)
        node.tick()
        node._status = NodeStatus.RUNNING
        assert _tick_until_done(node) == NodeStatus.SUCCESS
        assert get_default_bridge().owns_loop

    def test_executor_thread_pool_is_ignored(self):
        bridge = AsyncioBridge()
        try:
            node = _Sleeper("n", delay=0.0)
            node.set_bridge(bridge)
            node.set_thread_pool(object())  # executor injection — must be a no-op
            assert node._thread_pool is bridge
        finally:
            bridge.shutdown()

    def test_halt_is_cooperative_via_token(self):
        observed = {}

        class _Polling(CoroActionNode):
            async def execute_async(self, token):
                for _ in range(200):
                    if token.is_cancelled():
                        observed["token"] = True
                        return NodeStatus.FAILURE
                    await asyncio.sleep(0.01)
                return NodeStatus.SUCCESS

        bridge = AsyncioBridge()
        try:
            node = _Polling("n")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            time.sleep(0.05)
            node.halt()
            assert observed.get("token")
            assert node._future is None
            assert bridge.pending_tasks() == 0
        finally:
            bridge.shutdown()

    def test_halt_hard_cancels_coroutine_that_ignores_token(self):
        observed = {}
        unwound = threading.Event()

        class _Deaf(CoroActionNode):
            HALT_GRACE = 0.1

            async def execute_async(self, token):
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    observed["cancelled"] = True
                    unwound.set()
                    raise
                return NodeStatus.SUCCESS

        bridge = AsyncioBridge()
        try:
            node = _Deaf("n")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            time.sleep(0.05)
            node.halt()
            # halt() must not return before the coroutine has unwound
            assert unwound.is_set()
            assert observed.get("cancelled")
            assert node._future is None
        finally:
            bridge.shutdown()

    def test_halt_overrun_logs_a_warning(self, caplog):
        class _Deaf(CoroActionNode):
            HALT_GRACE = 0.05

            async def execute_async(self, token):
                await asyncio.sleep(30)
                return NodeStatus.SUCCESS

        bridge = AsyncioBridge()
        try:
            node = _Deaf("Deaf")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            time.sleep(0.02)
            with caplog.at_level(logging.WARNING, logger="bteng.nodes.leaf.coro_action"):
                node.halt()
            assert any(
                "did not stop within HALT_GRACE" in r.message and "Deaf" in r.message
                for r in caplog.records
            )
        finally:
            bridge.shutdown()

    def test_prompt_halt_logs_nothing(self, caplog):
        class _Polite(CoroActionNode):
            async def execute_async(self, token):
                while not token.is_cancelled():
                    await asyncio.sleep(0.005)
                return NodeStatus.FAILURE

        bridge = AsyncioBridge()
        try:
            node = _Polite("Polite")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            time.sleep(0.02)
            with caplog.at_level(logging.WARNING, logger="bteng.nodes.leaf.coro_action"):
                node.halt()
            assert caplog.records == []
        finally:
            bridge.shutdown()

    def test_default_halt_grace_is_short(self):
        # The halting thread blocks for this long — a long default silently
        # eats ticks on any node that ignores the token.
        assert CoroActionNode.HALT_GRACE <= 0.2

    def test_cancelled_coroutine_ticks_as_failure(self):
        class _Deaf(CoroActionNode):
            async def execute_async(self, token):
                await asyncio.sleep(30)
                return NodeStatus.SUCCESS

        bridge = AsyncioBridge()
        try:
            node = _Deaf("n")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            time.sleep(0.05)
            assert bridge.cancel_task(node._future)
            time.sleep(0.05)
            assert node.tick() == NodeStatus.FAILURE
        finally:
            bridge.shutdown()

    def test_coro_action_factory(self):
        async def _work(node, token):
            await asyncio.sleep(0.0)
            return True

        bridge = AsyncioBridge()
        try:
            node = coro_action("inline", _work)
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            assert _tick_until_done(node) == NodeStatus.SUCCESS
        finally:
            bridge.shutdown()

    def test_runs_under_tree_executor(self):
        bridge = AsyncioBridge()
        try:
            node = _Sleeper("n", delay=0.03)
            node.set_bridge(bridge)
            ex = TreeExecutor(ExecutorConfig(tick_interval=0.0, halt_on_completion=True))
            ex.set_tree(_make_tree(node))
            status = None
            for _ in range(200):
                status = ex.tick_once()
                if status != NodeStatus.RUNNING:
                    break
                time.sleep(0.01)
            assert status == NodeStatus.SUCCESS
            assert node.started == 1
        finally:
            bridge.shutdown()

    def test_dead_loop_thread_is_detected(self):
        """SystemExit escapes Task.__step and kills run_forever()."""
        class Exiter(CoroActionNode):
            async def execute_async(self, token):
                raise SystemExit(3)

        bridge = AsyncioBridge()
        node = Exiter("x")
        node.set_bridge(bridge)
        node.tick()
        node._status = NodeStatus.RUNNING
        for _ in range(100):
            if not bridge.is_alive():
                break
            time.sleep(0.02)
        assert not bridge.is_alive()
        assert isinstance(bridge.loop_error, SystemExit)
        with pytest.raises(RuntimeError):
            bridge.submit(asyncio.sleep, 0.0)
        # the stranded node fails instead of ticking RUNNING forever
        assert node.tick() == NodeStatus.FAILURE

    def test_node_recovers_onto_a_live_bridge(self):
        bridge = AsyncioBridge()
        set_default_bridge(bridge)
        node = _Sleeper("n", delay=0.0)
        node.tick()
        node._status = NodeStatus.RUNNING
        assert _tick_until_done(node) == NodeStatus.SUCCESS

        shutdown_default_bridge()          # bridge the node cached is now dead
        node._status = NodeStatus.IDLE
        status = node.tick()               # must not raise out of the tick contract
        assert status in (NodeStatus.RUNNING, NodeStatus.SUCCESS)
        node._status = NodeStatus.RUNNING
        assert _tick_until_done(node) == NodeStatus.SUCCESS

    def test_stopped_attached_loop_fails_instead_of_hanging(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        time.sleep(0.05)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)

        bridge = AsyncioBridge(loop)
        assert not bridge.is_alive()
        node = _Sleeper("n", delay=0.0)
        node.set_bridge(bridge)
        assert node.tick() == NodeStatus.FAILURE
        assert bridge.pending_tasks() == 0
        loop.close()

    def test_halt_does_not_start_a_second_copy(self):
        starts = []

        class Stubborn(CoroActionNode):
            HALT_GRACE = 0.05
            JOIN_TIMEOUT = 0.05

            async def execute_async(self, token):
                starts.append(1)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.5)   # cleanup that itself awaits
                    raise

        bridge = AsyncioBridge()
        try:
            node = Stubborn("s")
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            time.sleep(0.05)
            node.halt()
            assert node._orphan is not None
            assert node.tick() == NodeStatus.FAILURE
            assert "unwinding" in node.feedback_message
            assert len(starts) == 1            # no concurrent duplicate
        finally:
            bridge.shutdown()

    def test_cancel_before_task_starts(self):
        ran = []

        async def _work():
            ran.append(1)
            await asyncio.sleep(0.5)

        bridge = AsyncioBridge()
        try:
            blocker = threading.Event()

            async def _block():
                blocker.wait()                 # wedge the loop thread

            bridge.submit(lambda: _block())
            time.sleep(0.05)
            fut = bridge.submit(_work)         # queued, task not started yet
            assert bridge.cancel_task(fut)
            blocker.set()
            time.sleep(0.2)
            assert ran == []                   # never executed
        finally:
            blocker.set()
            bridge.shutdown()

    def test_halt_on_loop_thread_never_blocks(self):
        """halt() from the loop thread must cancel, not block on the Future."""
        observed = {}

        async def main():
            bridge = AsyncioBridge.from_running_loop()
            node = _Sleeper("n", delay=30.0)
            node.set_bridge(bridge)
            node.tick()
            node._status = NodeStatus.RUNNING
            await asyncio.sleep(0.05)

            t0 = time.monotonic()
            node.halt()                        # must not raise, must not block
            observed["halt_seconds"] = time.monotonic() - t0
            observed["future_cleared"] = node._future is None

            try:
                bridge.wait_all(timeout=1.0)   # this one still must refuse
            except RuntimeError as exc:
                observed["wait_all"] = str(exc)

        asyncio.run(main())
        assert observed["halt_seconds"] < 0.5      # no HALT_GRACE/JOIN_TIMEOUT stall
        assert observed["future_cleared"]
        assert "event loop thread" in observed.get("wait_all", "")

    def test_decorator_halt_on_loop_thread_does_not_raise(self):
        """A Timeout halting its coro child while ticking ON the loop thread."""
        from bteng.nodes.decorators.timeout import Timeout

        statuses = []

        class Slow(CoroActionNode):
            async def execute_async(self, token):
                await asyncio.sleep(30)
                return NodeStatus.SUCCESS

        async def main():
            set_default_bridge(AsyncioBridge(asyncio.get_running_loop()))
            node = Timeout("t", child=Slow("slow"), duration=0.05)
            for _ in range(20):
                statuses.append(node.execute_tick())   # must never raise
                if statuses[-1] != NodeStatus.RUNNING:
                    break
                await asyncio.sleep(0.02)

        asyncio.run(main())
        assert statuses[-1] == NodeStatus.FAILURE   # timed out, cleanly

    def test_discard_settles_pending_count_once(self):
        bridge = AsyncioBridge()
        try:
            fut = bridge.submit(asyncio.sleep, 0.5)
            assert bridge.pending_tasks() == 1
            assert bridge.discard(fut)
            assert bridge.pending_tasks() == 0
            assert bridge.discard(fut)              # idempotent
            assert bridge.pending_tasks() == 0
            fut.result(timeout=5.0)                 # done-callback must not double-count
            assert bridge.pending_tasks() == 0
        finally:
            bridge.shutdown()

    def test_dead_loop_does_not_leak_pending_count(self):
        class Exiter(CoroActionNode):
            async def execute_async(self, token):
                raise SystemExit(2)

        bridge = AsyncioBridge()
        node = Exiter("x")
        node.set_bridge(bridge)
        node.tick()
        node._status = NodeStatus.RUNNING
        for _ in range(100):
            if not bridge.is_alive():
                break
            time.sleep(0.02)
        assert node.tick() == NodeStatus.FAILURE
        assert bridge.pending_tasks() == 0

    def test_submit_shutdown_race_leaves_no_unresolved_future(self):
        for _ in range(60):
            bridge = AsyncioBridge()
            futures = []
            errors = []

            def _submitter():
                for _ in range(20):
                    try:
                        futures.append(bridge.submit(asyncio.sleep, 0.01))
                    except RuntimeError:
                        errors.append(1)       # expected once shut down

            t = threading.Thread(target=_submitter)
            t.start()
            time.sleep(0.005)
            bridge.shutdown()
            t.join(timeout=5.0)
            unresolved = [f for f in futures if not f.done()]
            assert not unresolved, f"{len(unresolved)} future(s) never resolved"

    def test_host_owned_loop_integration(self):
        """The realistic case: an asyncio app owns the loop, BT ticks on a thread."""
        results = {}

        class _Host(CoroActionNode):
            async def execute_async(self, token):
                await asyncio.sleep(0.01)
                results["loop"] = asyncio.get_running_loop()
                return NodeStatus.SUCCESS

        async def main():
            host_loop = asyncio.get_running_loop()
            set_default_bridge(AsyncioBridge(host_loop))
            node = _Host("n")

            def _tick_loop():
                node.tick()
                node._status = NodeStatus.RUNNING
                return _tick_until_done(node)

            status = await asyncio.to_thread(_tick_loop)
            assert status == NodeStatus.SUCCESS
            assert results["loop"] is host_loop

        asyncio.run(main())
