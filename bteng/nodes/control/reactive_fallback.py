"""Reactive Fallback control node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Tuple

from bteng.core.node import ControlNode, NodeStatus, NodeType

if TYPE_CHECKING:
    from bteng.blackboard.blackboard import Blackboard
    from bteng.core.node import TreeNode


def _is_condition_guard(node: "TreeNode") -> bool:
    """True if *node* is a condition, or a wrapper whose leaves are all conditions.

    ``<Inverter><IsStuck/></Inverter>`` is the ordinary way to write a negated
    guard, and a ``<Sequence>`` of conditions is an ordinary conjunction; both
    are conditions as far as the reactive contract is concerned.  Keying only
    off ``node_type is CONDITION`` would leave exactly those guards frozen —
    which is how the most common form of the idiom would keep the bug.

    Conditions are side-effect-free by BT convention, so re-ticking such a
    subtree every tick is safe; an ACTION anywhere underneath makes it not a
    guard, and it is then left to the dirty flag.
    """
    if node.node_type is NodeType.CONDITION:
        return True
    children = node.get_children()
    return bool(children) and all(_is_condition_guard(c) for c in children)


class ReactiveFallbackNode(ControlNode):
    """Re-evaluates every higher-priority condition, on every tick.

    A higher-priority child succeeding can interrupt a running lower-priority child.
    * Child SUCCESS → halt subsequent children, return SUCCESS.
    * Child RUNNING → halt subsequent children, return RUNNING.
    * All FAILURE → return FAILURE.

    THE REACTIVE CONTRACT, PRECISELY
    --------------------------------
    Every *guard* child is re-ticked on every tick, unconditionally.  A guard is
    a CONDITION, or a wrapper (Inverter, a Sequence of conditions, …) whose
    leaves are all conditions — see _is_condition_guard.  That is the guarantee
    the node exists to provide, and it must not depend on where the condition
    reads its truth from: a ROS topic, a sensor handle or a clock is as valid as
    a blackboard key.

    What the dirty flag optimises is only the *action* children ahead of the
    running one that already returned FAILURE: re-entering them is expensive and
    often not idempotent, so while an action child is RUNNING this node
    subscribes to every Blackboard reachable from its subtree and skips those
    preceding actions until some key is written.  A blackboard write sets the
    dirty flag and the next tick re-evaluates from the first child.

    (Previously the fast path skipped conditions too, so a condition backed by
    anything other than a blackboard was never re-evaluated while a lower
    priority action ran — and merely giving a child a Blackboard silently
    switched the node between reactive and non-reactive semantics.)
    """

    def __init__(self, name: str = "ReactiveFallback", children=None, config=None):
        super().__init__(name, children or [], config)
        self._running_child_idx: Optional[int] = None
        self._dirty: bool = True
        self._bb_subscriptions: List[Tuple["Blackboard", int]] = []

    def tick(self) -> NodeStatus:
        if self._dirty or self._running_child_idx is None or not self._bb_subscriptions:
            return self._full_eval()
        if self._running_child_idx >= len(self._children):
            # The child list shrank (runtime tree modification) while we held a
            # cursor into it.  Re-evaluating from scratch is always safe.
            return self._full_eval()
        return self._fast_tick()

    def _full_eval(self) -> NodeStatus:
        self._dirty = False
        for i, child in enumerate(self._children):
            status = child.execute_tick()

            if status == NodeStatus.SUCCESS:
                self._halt_from(i + 1)
                self._clear_running()
                return NodeStatus.SUCCESS

            if status == NodeStatus.RUNNING:
                self._halt_from(i + 1)
                if self._running_child_idx is None:
                    self._subscribe()
                self._running_child_idx = i
                return NodeStatus.RUNNING

        self._clear_running()
        return NodeStatus.FAILURE

    def _fast_tick(self) -> NodeStatus:
        idx = self._running_child_idx

        # The reactive guarantee: higher-priority conditions are re-evaluated
        # even when no blackboard key changed.
        for i in range(idx):
            child = self._children[i]
            if not _is_condition_guard(child):
                continue
            status = child.execute_tick()
            if status == NodeStatus.SUCCESS:
                self._halt_from(i + 1)
                self._clear_running()
                return NodeStatus.SUCCESS
            if status == NodeStatus.RUNNING:
                self._halt_from(i + 1)
                self._running_child_idx = i
                return NodeStatus.RUNNING

        status = self._children[idx].execute_tick()

        if status == NodeStatus.RUNNING:
            return NodeStatus.RUNNING

        # Running child completed — exit fast path
        self._unsubscribe()
        self._running_child_idx = None

        if status == NodeStatus.SUCCESS:
            self._halt_from(idx + 1)
            return NodeStatus.SUCCESS

        # FAILURE — continue evaluating remaining children
        for i in range(idx + 1, len(self._children)):
            status = self._children[i].execute_tick()
            if status == NodeStatus.SUCCESS:
                self._halt_from(i + 1)
                return NodeStatus.SUCCESS
            if status == NodeStatus.RUNNING:
                self._halt_from(i + 1)
                self._running_child_idx = i
                self._subscribe()
                return NodeStatus.RUNNING

        return NodeStatus.FAILURE

    def _on_halt(self) -> None:
        self._clear_running()

    def _on_reset(self) -> None:
        self._dirty = True

    def _clear_running(self) -> None:
        self._unsubscribe()
        self._running_child_idx = None

    def _subscribe(self) -> None:
        # NOTE: this registers a *bound method* in the blackboard's callback
        # table, so the blackboard keeps this node (and its whole subtree) alive
        # until _on_halt() or completion unsubscribes.  A RUNNING reactive node
        # that is simply dropped leaks.  The fix belongs in Blackboard.subscribe
        # (store a weakref and drop dead entries) and is owned elsewhere; do not
        # work around it here.
        if self._bb_subscriptions:
            return
        for bb in self._collect_blackboards():
            sub_id = bb.subscribe(self._mark_dirty)
            self._bb_subscriptions.append((bb, sub_id))

    def _unsubscribe(self) -> None:
        for bb, sub_id in self._bb_subscriptions:
            bb.unsubscribe(sub_id)
        self._bb_subscriptions.clear()

    def _mark_dirty(self, key: str, value: Any) -> None:
        self._dirty = True

    def _collect_blackboards(self) -> "List[Blackboard]":
        from bteng.blackboard.blackboard import Blackboard  # noqa: F401 (type guard)
        seen: set = set()
        result = []
        stack = list(self._children)
        while stack:
            node = stack.pop()
            bb = node._config.blackboard
            if bb is not None and id(bb) not in seen:
                seen.add(id(bb))
                result.append(bb)
            stack.extend(node.get_children())
        return result

    def _halt_from(self, idx: int) -> None:
        for j in range(idx, len(self._children)):
            self._children[j].halt()
