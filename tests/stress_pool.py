"""Stress harness for the TreeExecutor ThreadPool — run manually, not by pytest.

    python3 tests/stress_pool.py

Deliberately not named test_*.py: it takes ~20s and asserts on timing, which
does not belong in the unit suite.

Targets the risk the shared pool introduces: AsyncActionNodes used to get an
unbounded supply of daemon threads and now share a bounded pool.  Anything that
depended on "every async node starts immediately" can now starve.
"""
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bteng.concurrency.thread_pool import ThreadPool
from bteng.core.executor import ExecutorConfig, TreeExecutor
from bteng.core.node import NodeConfig, NodeStatus
from bteng.core.tree import Tree, TreeMetadata
from bteng.nodes.control.parallel import ParallelNode
from bteng.nodes.control.sequence import SequenceNode
from bteng.nodes.leaf.action import ActionNode
from bteng.nodes.leaf.async_action import AsyncActionNode

RESULTS = []


def scenario(fn):
    def run():
        name = fn.__name__
        t0 = time.perf_counter()
        try:
            detail = fn() or ""
            RESULTS.append(("PASS", name, f"{time.perf_counter()-t0:.2f}s {detail}"))
        except BaseException as exc:
            RESULTS.append(("FAIL", name, f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
    run.__name__ = fn.__name__
    return run


def _tree(root):
    return Tree(TreeMetadata(id="stress"), root)


def _par(name, children):
    cfg = NodeConfig(params={"success_threshold": -1})   # -1 → all children
    return ParallelNode(name, children=children, config=cfg)


class Sleeper(AsyncActionNode):
    def __init__(self, name, seconds=0.05):
        super().__init__(name)
        self.seconds = seconds
        self.started = threading.Event()
        self.thread_name = None

    def execute_async(self, token):
        self.thread_name = threading.current_thread().name
        self.started.set()
        time.sleep(self.seconds)
        return NodeStatus.SUCCESS


class Sync(ActionNode):
    def tick(self):
        return NodeStatus.SUCCESS


def _drain(ex, max_ticks=4000, period=0.002):
    for _ in range(max_ticks):
        st = ex.tick_once()
        if st != NodeStatus.RUNNING:
            return st
        time.sleep(period)
    return NodeStatus.RUNNING


# ── 1. The starvation risk: more parallel async nodes than pool threads ──────

@scenario
def s01_parallel_wider_than_pool():
    """16 async leaves, pool of 4. All must still complete."""
    nodes = [Sleeper(f"n{i}", 0.05) for i in range(16)]
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0, thread_pool_size=4))
    ex.set_tree(_tree(_par("par", nodes)))
    try:
        st = _drain(ex)
        assert st == NodeStatus.SUCCESS, st
        assert all(n.started.is_set() for n in nodes)
        pooled = sum(n.thread_name.startswith("bteng-worker") for n in nodes)
        return f"{pooled}/16 on pool threads"
    finally:
        ex.shutdown()


@scenario
def s02_pool_of_one():
    """Pathological: pool of 1, 8 parallel leaves. Serialises but must finish."""
    nodes = [Sleeper(f"n{i}", 0.02) for i in range(8)]
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0, thread_pool_size=1))
    ex.set_tree(_tree(_par("par", nodes)))
    try:
        st = _drain(ex)
        assert st == NodeStatus.SUCCESS, st
        return "8 leaves through a 1-thread pool"
    finally:
        ex.shutdown()


@scenario
def s03_interdependent_nodes_deadlock_probe():
    """THE regression to fear.

    Waiter blocks until Signaller runs.  With unbounded threads both start and
    it resolves.  With a pool of 1, Waiter occupies the only worker and
    Signaller can never run — a deadlock that did not exist before.
    """
    gate = threading.Event()

    class Waiter(AsyncActionNode):
        def execute_async(self, token):
            for _ in range(200):                       # 2s ceiling
                if gate.wait(0.01) or token.is_cancelled():
                    return NodeStatus.SUCCESS
            return NodeStatus.FAILURE

    class Signaller(AsyncActionNode):
        def execute_async(self, token):
            gate.set()
            return NodeStatus.SUCCESS

    nodes = [Waiter("waiter"), Signaller("signaller")]
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0, thread_pool_size=1))
    ex.set_tree(_tree(_par("par", nodes)))
    try:
        st = _drain(ex, max_ticks=1500)
        return f"pool=1 -> {st.name} (FAILURE == starvation confirmed)"
    finally:
        ex.shutdown()


@scenario
def s04_interdependent_nodes_default_pool():
    """Same shape at the default size of 4 — must resolve."""
    gate = threading.Event()

    class Waiter(AsyncActionNode):
        def execute_async(self, token):
            return NodeStatus.SUCCESS if gate.wait(2.0) else NodeStatus.FAILURE

    class Signaller(AsyncActionNode):
        def execute_async(self, token):
            time.sleep(0.02)
            gate.set()
            return NodeStatus.SUCCESS

    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0, thread_pool_size=4))
    ex.set_tree(_tree(_par("par", [Waiter("w"), Signaller("s")])))
    try:
        st = _drain(ex)
        assert st == NodeStatus.SUCCESS, st
        return "resolved at pool=4"
    finally:
        ex.shutdown()


# ── 2. Thread hygiene ────────────────────────────────────────────────────────

@scenario
def s05_no_thread_leak_over_many_runs():
    base = threading.active_count()
    peak = base
    for _ in range(60):
        ex = TreeExecutor(ExecutorConfig(tick_interval=0.0))
        ex.set_tree(_tree(_par("par", [Sleeper(f"n{i}", 0.005) for i in range(6)])))
        _drain(ex)
        peak = max(peak, threading.active_count())
        ex.shutdown()
    time.sleep(0.3)
    end = threading.active_count()
    assert end <= base + 2, f"leak: base={base} end={end}"
    return f"base={base} peak={peak} end={end}"


@scenario
def s06_old_behaviour_still_available():
    """thread_pool_size=0 must reproduce the pre-change daemon-thread model."""
    nodes = [Sleeper(f"n{i}", 0.05) for i in range(12)]
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0, thread_pool_size=0))
    ex.set_tree(_tree(_par("par", nodes)))
    try:
        st = _drain(ex)
        assert st == NodeStatus.SUCCESS, st
        assert ex._thread_pool is None
        assert not any(n.thread_name.startswith("bteng-worker") for n in nodes)
        return "12 concurrent daemon threads, no pool"
    finally:
        ex.shutdown()


# ── 3. Lifecycle: halt, shutdown, restart ────────────────────────────────────

@scenario
def s07_halt_restart_cycles():
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0))
    node = Sleeper("slow", 0.3)
    ex.set_tree(_tree(SequenceNode("root", children=[node])))
    try:
        for i in range(40):
            ex.tick_once()
            ex.halt_tree()
            ex.reset_tree()
        return "40 halt/reset cycles"
    finally:
        ex.shutdown()


@scenario
def s08_shutdown_then_tick_again():
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0))
    node = Sleeper("n", 0.01)
    ex.set_tree(_tree(node))
    try:
        for i in range(25):
            _drain(ex)
            ex.reset_tree()
            ex.shutdown()             # disposes the pool each round
        return "25 shutdown/re-tick rounds, pool rebuilt each time"
    finally:
        ex.shutdown()


@scenario
def s09_event_loop_mode():
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.001))
    done = threading.Event()
    ex.on_completion(lambda s: done.set())
    ex.set_tree(_tree(_par("par", [Sleeper(f"n{i}", 0.02) for i in range(8)])))
    ex.start_event_loop()
    assert done.wait(10.0), "event loop never completed"
    ex.stop_event_loop()
    assert ex._thread_pool is None, "stop_event_loop left the pool alive"
    return f"final={ex.final_status.name}"


@scenario
def s10_supplied_pool_survives_shutdown():
    pool = ThreadPool(num_threads=6)
    try:
        for _ in range(20):
            ex = TreeExecutor(ExecutorConfig(tick_interval=0.0))
            ex.set_thread_pool(pool)
            ex.set_tree(_tree(_par("par", [Sleeper(f"n{i}", 0.005) for i in range(6)])))
            _drain(ex)
            ex.shutdown()
            assert not pool._stopped, "executor killed a pool it does not own"
        assert pool.submit(lambda: 42).result() == 42
        return "20 executors shared one caller-owned pool"
    finally:
        pool.shutdown()


# ── 4. Throughput soak ───────────────────────────────────────────────────────

@scenario
def s11_soak():
    ex = TreeExecutor(ExecutorConfig(tick_interval=0.0))
    nodes = [Sleeper(f"n{i}", 0.0) for i in range(8)]
    ex.set_tree(_tree(_par("par", nodes)))
    try:
        runs = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 3.0:
            _drain(ex)
            ex.reset_tree()
            runs += 1
        rate = runs * 8 / (time.perf_counter() - t0)
        assert threading.active_count() < 40, threading.active_count()
        return f"{runs} tree runs, ~{rate:.0f} async-node completions/s"
    finally:
        ex.shutdown()


@scenario
def s12_mixed_sync_async_coro():
    import asyncio
    from bteng.concurrency.asyncio_bridge import AsyncioBridge, set_default_bridge
    from bteng.nodes.leaf.coro_action import CoroActionNode

    class Coro(CoroActionNode):
        async def execute_async(self, token):
            await asyncio.sleep(0.01)
            return NodeStatus.SUCCESS

    bridge = AsyncioBridge()
    set_default_bridge(bridge)
    try:
        for _ in range(30):
            ex = TreeExecutor(ExecutorConfig(tick_interval=0.0))
            ex.set_tree(_tree(_par("par", [
                Sync("sync"), Sleeper("async", 0.01), Coro("coro"),
            ])))
            st = _drain(ex)
            assert st == NodeStatus.SUCCESS, st
            assert ex._thread_pool is not None      # the AsyncActionNode wants one
            ex.shutdown()
        return "30 mixed trees"
    finally:
        set_default_bridge(None)
        bridge.shutdown()


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("s") and callable(v) and k[1:3].isdigit()]:
        fn()
    print("\n" + "=" * 74)
    for status, name, detail in RESULTS:
        print(f"{status}  {name:42s} {detail}")
    fails = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print("=" * 74)
    print(f"{len(RESULTS) - fails}/{len(RESULTS)} passed")
    sys.exit(1 if fails else 0)
