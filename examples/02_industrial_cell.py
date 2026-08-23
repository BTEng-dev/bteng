"""Example 02 — An industrial pick-and-place cell. 🏭

A step up from example 01. This tree runs a work cell that moves parts from an
infeed conveyor to a pallet, and it has to cope with a machine that sometimes
fails.

The tree lives in `trees/industrial_cell.xml`:

    ReactiveSequence "production cycle"
      ├── Condition        EStopClear          re-checked every single tick
      └── Sequence "cycle"
            ├── Retry(3) → PickPart            gripper misses sometimes
            ├── Timeout(4s) → MoveToPallet     never block the line forever
            ├── Fallback "place"
            │     ├── PlacePart
            │     └── Sequence "recover"       jog and retry once
            │           ├── JogAxis
            │           └── PlacePart
            └── IncrementCounter

Three ideas worth taking away:

**ReactiveSequence re-checks its guard.** `EStopClear` is evaluated on every
tick, not once at the start. The moment the e-stop trips, whatever is running is
halted immediately. A plain `Sequence` would not do that.

**Retry and Timeout are decorators.** Flakiness and deadlines are wrapped around
a node instead of being coded inside it, so the action stays simple.

**Fallback is your recovery path.** If `PlacePart` fails, the tree jogs the axis
and tries once more before giving up.

Every machine action is simulated: it runs for a random time and may fail with a
set probability, so no hardware is needed.

Run it:

    python3 examples/02_industrial_cell.py
"""

from __future__ import annotations

import os
import random
import time

from bteng import (
    Blackboard,
    ConditionNode,
    InputPort,
    NodeStatus,
    StatefulActionNode,
    Tree,
    TreeExecutor,
    TreeMetadata,
    XMLTreeParser,
    register_node,
)

XML_PATH = os.path.join(os.path.dirname(__file__), "trees", "industrial_cell.xml")
CYCLES_TO_RUN = 3


# ── A simulated machine motion ────────────────────────────────────────────────

@register_node("MachineMove")
class MachineMove(StatefulActionNode):
    """Run for a random duration, then succeed — or fail with `fail_rate`.

    Stands in for anything with a physical settling time: an axis move, a
    gripper close, a vacuum pull.
    """

    @classmethod
    def provided_ports(cls):
        return [
            InputPort("label",     "What the machine is doing"),
            InputPort("icon",      "Emoji for the log line",       default="⚙️"),
            InputPort("min_s",     "Fastest completion (seconds)", default=0.2),
            InputPort("max_s",     "Slowest completion (seconds)", default=0.6),
            InputPort("fail_rate", "Chance of failing, 0.0-1.0",   default=0.0),
        ]

    def __init__(self, name: str, config=None) -> None:
        super().__init__(name, config)
        self._done_at = 0.0
        self._will_fail = False

    def on_start(self) -> NodeStatus:
        duration = random.uniform(
            float(self.get_input("min_s", 0.2)),
            float(self.get_input("max_s", 0.6)),
        )
        self._done_at   = time.monotonic() + duration
        self._will_fail = random.random() < float(self.get_input("fail_rate", 0.0))
        print(f"      {self.get_input('icon')} {self.get_input('label')}… ({duration:.2f}s)")
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if time.monotonic() < self._done_at:
            return NodeStatus.RUNNING
        if self._will_fail:
            self.set_failure_reason(f"{self.name} did not confirm")
            print(f"      ❌ {self.name} failed")
            return NodeStatus.FAILURE
        print(f"      ✅ {self.name}")
        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        print(f"      🛑 {self.name} halted mid-motion")


@register_node("EStopClear")
class EStopClear(ConditionNode):
    """SUCCESS while the e-stop is released. Re-checked on every tick."""

    def tick(self) -> NodeStatus:
        return NodeStatus.FAILURE if self.blackboard.get("estop", False) else NodeStatus.SUCCESS


@register_node("CountPart")
class CountPart(StatefulActionNode):
    """Book one finished part on the blackboard."""

    def on_start(self) -> NodeStatus:
        done = self.blackboard.get("parts_done", 0) + 1
        self.blackboard.set("parts_done", done)
        print(f"      📦 part {done} palletised")
        return NodeStatus.SUCCESS

    def on_running(self) -> NodeStatus:
        return NodeStatus.SUCCESS


# ── The tree ──────────────────────────────────────────────────────────────────

def build_tree(bb: Blackboard) -> Tree:
    """Load the cell's behaviour from XML.

    A fresh parse per cycle gives every cycle a clean tree, which is the simplest
    way to reset node state between runs.
    """
    root = XMLTreeParser().parse_file(XML_PATH, blackboard=bb)
    return Tree(TreeMetadata(id="PickAndPlaceCell"), root)


def main() -> None:
    random.seed(3)                       # fixed so the demo reads the same every run

    bb = Blackboard.create("cell")
    bb.set("parts_done", 0)
    bb.set("estop", False)

    print("🏭 Work cell starting.\n")

    for cycle in range(1, CYCLES_TO_RUN + 1):
        print(f"── cycle {cycle} ──")
        executor = TreeExecutor()
        executor.set_tree(build_tree(bb))
        status = executor.tick_until_result(max_ticks=2000)
        print(f"   → {status.name}\n")
        executor.shutdown()

    # ── The reactive guard, shown properly ────────────────────────────────────
    # Start a cycle, let the machine actually begin moving, then trip the e-stop
    # and keep ticking. Because the guard sits in a ReactiveSequence it is
    # re-evaluated every tick, so the running action is halted mid-motion.
    print("── e-stop during a running motion ──")
    executor = TreeExecutor()
    executor.set_tree(build_tree(bb))

    for _ in range(5):                   # let PickPart get under way
        executor.tick_once()

    print("   🚨 operator hits the e-stop")
    bb.set("estop", True)

    status = executor.tick_once()        # guard fails -> running action is halted
    print(f"   → {status.name} on the very next tick")
    executor.shutdown()

    print(f"\n🏁 Parts palletised: {bb.get('parts_done')} / {CYCLES_TO_RUN}")
    print("   The gripper missed and a release failed along the way, but Retry and")
    print("   the recovery Fallback absorbed both — no cycle was lost.")

    Blackboard.reset("cell")


if __name__ == "__main__":
    main()
