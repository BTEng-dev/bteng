"""Example 01 — Keeping a creature comfortable. 🐹

The smallest useful behavior tree: check a need, and act only if it is unmet.

The tree itself lives in `trees/creature_comfort.xml`:

    Sequence "stay comfortable"
      ├── Fallback "not hungry"        eat only if hungry
      │     ├── Condition  IsFull
      │     └── Action     Eat
      └── Fallback "not freezing"      warm up only if cold
            ├── Condition  IsWarm
            └── Action     WarmUp

Python here only registers the node types; the shape of the behaviour is data.
Edit the XML and the program changes without touching this file.

A Fallback succeeds as soon as one child succeeds. So when `IsFull` already
passes, `Eat` is never ticked — the tree does nothing and costs nothing. That is
the whole idea: a behavior tree asks before it acts.

The actions are simulated. Each returns RUNNING for a random duration before
succeeding, exactly as a real motor command or network call would.

Run it:

    python3 examples/01_creature_comfort.py
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

XML_PATH = os.path.join(os.path.dirname(__file__), "trees", "creature_comfort.xml")


# ── A simulated action ────────────────────────────────────────────────────────

@register_node("SatisfyNeed")
class SatisfyNeed(StatefulActionNode):
    """Take a random amount of time, then mark a need as met.

    `StatefulActionNode` replaces a single `tick()` with three hooks:

        on_start()    once, when the node goes IDLE -> RUNNING
        on_running()  every tick after that
        on_halted()   if something interrupts the node mid-work

    That split is what makes long-running work safe to write.
    """

    @classmethod
    def provided_ports(cls):
        return [
            InputPort("need",  "Blackboard key to set once the work finishes"),
            InputPort("label", "Text for the log line", default="working"),
            InputPort("icon",  "Emoji for the log line", default="⚙️"),
        ]

    def __init__(self, name: str, config=None) -> None:
        super().__init__(name, config)
        self._done_at = 0.0

    def on_start(self) -> NodeStatus:
        seconds = random.uniform(0.3, 0.9)
        self._done_at = time.monotonic() + seconds
        print(f"   {self.get_input('icon')} {self.get_input('label')}… (~{seconds:.1f}s)")
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if time.monotonic() < self._done_at:
            return NodeStatus.RUNNING
        self.blackboard.set(self.get_input("need"), True)
        print(f"   ✅ {self.name} done")
        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        print(f"   ⚠️  {self.name} interrupted before finishing")


@register_node("NeedIsMet")
class NeedIsMet(ConditionNode):
    """SUCCESS when the named need is already satisfied on the blackboard."""

    @classmethod
    def provided_ports(cls):
        return [InputPort("need", "Blackboard key holding the need's state")]

    def tick(self) -> NodeStatus:
        met = bool(self.blackboard.get(self.get_input("need"), False))
        return NodeStatus.SUCCESS if met else NodeStatus.FAILURE


# ── The tree ──────────────────────────────────────────────────────────────────

def main() -> None:
    bb = Blackboard.create("creature")
    bb.set("is_full", False)     # starts hungry
    bb.set("is_warm", True)      # but warm enough

    root = XMLTreeParser().parse_file(XML_PATH, blackboard=bb)
    tree = Tree(TreeMetadata(id="StayComfortable"), root)

    executor = TreeExecutor()
    executor.set_tree(tree)

    print("🐹 The creature wakes up: hungry, but warm enough.\n")
    status = executor.tick_until_result(max_ticks=500)

    print(f"\n🌳 Tree finished: {status.name}")
    print(f"   is_full={bb.get('is_full')}  is_warm={bb.get('is_warm')}")
    print("\nWarmUp never ran: IsWarm already passed, so that Fallback was")
    print("satisfied without ticking its second child.")

    executor.shutdown()
    Blackboard.reset("creature")


if __name__ == "__main__":
    main()
