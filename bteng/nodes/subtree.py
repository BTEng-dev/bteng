"""SubTree node — reusable tree composition with port remapping."""

from __future__ import annotations

from bteng.core.node import DecoratorNode, NodeStatus, TreeNode


class SubTree(DecoratorNode):
    """Wraps another tree's root, optionally with a scoped Blackboard.

    The XML parser creates a child Blackboard with port remappings and
    passes the subtree root here. From the engine's perspective this is
    just a transparent pass-through decorator.
    """

    def __init__(self, name: str, child: TreeNode, config=None):
        super().__init__(name, child, config)

    def tick(self) -> NodeStatus:
        return self._child.execute_tick()

    # halt() is deliberately NOT overridden: DecoratorNode.halt() already halts
    # the child and resets the status, *and* calls _on_halt() first.  An
    # override that skipped _on_halt() silently dropped every subclass's
    # cleanup hook — the exact bug TestBug2HaltLifecycle guards against for
    # ControlNode and DecoratorNode.
