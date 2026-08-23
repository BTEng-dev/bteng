"""TreeExecutor's shared ThreadPool — built lazily, injected, disposed.

``ExecutorConfig.thread_pool_size`` used to be dead: nothing ever constructed a
ThreadPool, so every AsyncActionNode fell back to spawning its own daemon thread
while the docs claimed the executor injected a shared pool.
"""
from __future__ import annotations

import threading
import time

from bteng.concurrency.thread_pool import ThreadPool
from bteng.core.executor import ExecutorConfig, TreeExecutor
from bteng.core.node import NodeStatus
from bteng.core.tree import Tree, TreeMetadata
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.leaf.action import ActionNode
from bteng.nodes.leaf.async_action import AsyncActionNode
from bteng.nodes.leaf.coro_action import CoroActionNode


def _tree(root):
    return Tree(TreeMetadata(id="test"), root)


def _executor(**kwargs):
    return TreeExecutor(ExecutorConfig(tick_interval=0.0, **kwargs))


class _Sync(ActionNode):
    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS


class _Worker(AsyncActionNode):
    """Records the thread it ran on, so pool vs daemon-thread is observable."""

    def __init__(self, name="w"):
        super().__init__(name)
        self.thread_names = []

    def execute_async(self, token) -> NodeStatus:
        self.thread_names.append(threading.current_thread().name)
        return NodeStatus.SUCCESS


class _Coro(CoroActionNode):
    async def execute_async(self, token) -> NodeStatus:
        return NodeStatus.SUCCESS


def _drain(ex, node, max_ticks=200):
    for _ in range(max_ticks):
        if ex.tick_once() != NodeStatus.RUNNING:
            return
        time.sleep(0.005)


class TestPoolCreation:

    def test_async_node_runs_on_the_shared_pool(self):
        ex = _executor()
        node = _Worker()
        ex.set_tree(_tree(node))
        try:
            _drain(ex, node)
            assert node.thread_names, "execute_async never ran"
            assert node.thread_names[0].startswith("bteng-worker")
        finally:
            ex.shutdown()

    def test_pool_honours_configured_size(self):
        ex = _executor(thread_pool_size=2)
        ex.set_tree(_tree(_Worker()))
        try:
            ex.tick_once()
            assert ex._thread_pool is not None
            assert ex._thread_pool.thread_count == 2
        finally:
            ex.shutdown()

    def test_no_pool_for_a_tree_that_does_not_need_one(self):
        # Four idle worker threads for a tree of plain sync nodes is waste.
        ex = _executor()
        ex.set_tree(_tree(SequenceNode("root", children=[_Sync("a"), _Sync("b")])))
        try:
            ex.tick_once()
            assert ex._thread_pool is None
        finally:
            ex.shutdown()

    def test_no_pool_for_a_coro_only_tree(self):
        # CoroActionNode runs on an event loop; it declines the pool.
        ex = _executor()
        ex.set_tree(_tree(_Coro("c")))
        try:
            ex.tick_once()
            assert ex._thread_pool is None
        finally:
            ex.shutdown()

    def test_pool_built_for_a_nested_async_node(self):
        node = _Worker()
        ex = _executor()
        ex.set_tree(_tree(SequenceNode("root", children=[_Sync("a"), node])))
        try:
            ex.tick_once()
            assert ex._thread_pool is not None
        finally:
            ex.shutdown()

    def test_size_zero_disables_the_pool(self):
        ex = _executor(thread_pool_size=0)
        node = _Worker()
        ex.set_tree(_tree(node))
        try:
            _drain(ex, node)
            assert ex._thread_pool is None
            assert node.thread_names
            assert not node.thread_names[0].startswith("bteng-worker")
        finally:
            ex.shutdown()


class TestSaturation:
    """A bounded pool is a behavior change: async leaves used to get a thread each."""

    def test_more_parallel_leaves_than_threads_still_all_complete(self):
        from bteng.core.node import NodeConfig
        from bteng.nodes.control.parallel import ParallelNode

        nodes = [_Worker(f"n{i}") for i in range(12)]
        par = ParallelNode(
            "par", children=nodes, config=NodeConfig(params={"success_threshold": -1})
        )
        ex = _executor(thread_pool_size=3)
        ex.set_tree(_tree(par))
        try:
            for _ in range(500):
                if ex.tick_once() != NodeStatus.RUNNING:
                    break
                time.sleep(0.002)
            assert all(n.thread_names for n in nodes), "some leaf never ran"
        finally:
            ex.shutdown()

    def test_a_leaf_that_blocks_on_another_leaf_can_starve(self):
        """Documents the hazard rather than pretending it away.

        Waiter holds the only worker; Signaller can never be scheduled to
        release it. With unbounded daemon threads this pair resolved.
        """
        from bteng.core.node import NodeConfig
        from bteng.nodes.control.parallel import ParallelNode

        gate = threading.Event()

        class _Waiter(AsyncActionNode):
            got_signal = None

            def execute_async(self, token) -> NodeStatus:
                # Occupies the only worker for the whole wait.
                _Waiter.got_signal = gate.wait(0.2)
                return NodeStatus.SUCCESS if _Waiter.got_signal else NodeStatus.FAILURE

        class _Signaller(AsyncActionNode):
            def execute_async(self, token) -> NodeStatus:
                gate.set()
                return NodeStatus.SUCCESS

        par = ParallelNode(
            "par",
            children=[_Waiter("w"), _Signaller("s")],
            config=NodeConfig(params={"success_threshold": -1}),
        )
        ex = _executor(thread_pool_size=1)
        ex.set_tree(_tree(par))
        try:
            for _ in range(400):
                if ex.tick_once() != NodeStatus.RUNNING:
                    break
                time.sleep(0.002)
            # Signaller could not be scheduled while the waiter held the worker,
            # so the waiter timed out. With unbounded threads it would have
            # received the signal immediately.
            assert _Waiter.got_signal is False, (
                "the waiter got its signal despite a saturated pool — if so, the "
                "starvation hazard documented in docs/reference/concurrency.md is gone"
            )
        finally:
            ex.shutdown()


class TestPoolOwnership:

    def test_shutdown_disposes_the_pool_it_built(self):
        ex = _executor()
        ex.set_tree(_tree(_Worker()))
        ex.tick_once()
        pool = ex._thread_pool
        assert pool is not None
        ex.shutdown()
        assert ex._thread_pool is None
        assert pool._stopped

    def test_shutdown_leaves_a_caller_supplied_pool_alone(self):
        pool = ThreadPool(num_threads=2)
        try:
            ex = _executor()
            ex.set_thread_pool(pool)
            ex.set_tree(_tree(_Worker()))
            ex.tick_once()
            assert ex._thread_pool is pool
            ex.shutdown()
            assert not pool._stopped      # the caller still owns it
            assert pool.submit(lambda: 1).result() == 1
        finally:
            pool.shutdown()

    def test_set_thread_pool_disposes_a_previously_owned_pool(self):
        ex = _executor()
        ex.set_tree(_tree(_Worker()))
        ex.tick_once()
        owned = ex._thread_pool
        assert owned is not None

        supplied = ThreadPool(num_threads=1)
        try:
            ex.set_thread_pool(supplied)
            assert owned._stopped         # not leaked
            assert ex._thread_pool is supplied
        finally:
            ex.shutdown()
            supplied.shutdown()

    def test_pool_is_rebuilt_after_shutdown(self):
        ex = _executor()
        node = _Worker()
        ex.set_tree(_tree(node))
        ex.tick_once()
        first = ex._thread_pool
        ex.shutdown()

        try:
            _drain(ex, node)              # ticking again must not use the dead pool
            assert ex._thread_pool is not first
            assert node.thread_names[-1].startswith("bteng-worker")
        finally:
            ex.shutdown()

    def test_nodes_stop_referencing_a_disposed_pool(self):
        ex = _executor()
        node = _Worker()
        ex.set_tree(_tree(node))
        ex.tick_once()
        ex.shutdown()
        assert node._thread_pool is None  # else the next tick submits to a dead pool
