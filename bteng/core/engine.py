"""Behavior Tree execution engine."""

from __future__ import annotations

import time
from typing import Optional

from bteng.core.node import NodeStatus, TreeNode
from bteng.blackboard.blackboard import Blackboard
from bteng.logging.tracer import ExecutionTracer


class BehaviorTreeEngine:
    """Tick-based execution engine for a Behavior Tree.

    Usage::

        engine = BehaviorTreeEngine(root, blackboard=bb, hz=10.0)
        status = engine.tick_once()
        final  = engine.run_until_complete()
    """

    def __init__(
        self,
        root: TreeNode,
        blackboard: Optional[Blackboard] = None,
        tracer: Optional[ExecutionTracer] = None,
        hz: Optional[float] = None,
    ) -> None:
        self._root = root
        self._blackboard = blackboard or Blackboard.create()
        self._tracer = tracer
        self._hz = hz
        self._tick_count = 0

        # Wire the blackboard into the tree. Without this the constructor's own
        # documented usage -- BehaviorTreeEngine(root, blackboard=bb) -- left
        # every node with blackboard=None, so get_input()/set_output() silently
        # resolved to params/nothing while engine.blackboard happily returned
        # the object you passed. Only from_xml() used to wire it, via the parser.
        self._inject_blackboard(root, self._blackboard)

        if tracer is not None:
            self._inject_tracer(root, tracer)

    # ------------------------------------------------------------------
    # Tick interface
    # ------------------------------------------------------------------

    def tick_once(self) -> NodeStatus:
        """Execute one tick of the tree."""
        self._tick_count += 1
        return self._root.execute_tick()

    def run_until_complete(
        self,
        max_ticks: Optional[int] = None,
        interval: Optional[float] = None,
    ) -> NodeStatus:
        """Tick until tree returns SUCCESS or FAILURE.

        Args:
            max_ticks: Stop after this many ticks (returns last status).
            interval:  Sleep between ticks (seconds). Overrides hz if set.
        """
        sleep_time = interval if interval is not None else (1.0 / self._hz if self._hz else None)
        count = 0
        while True:
            status = self.tick_once()
            count += 1
            if status != NodeStatus.RUNNING:
                return status
            if max_ticks is not None and count >= max_ticks:
                return status
            if sleep_time:
                time.sleep(sleep_time)

    def tick_while_running(self, max_ticks: Optional[int] = None) -> NodeStatus:
        """Alias for run_until_complete (no sleep)."""
        return self.run_until_complete(max_ticks=max_ticks, interval=0)

    def halt(self) -> None:
        """Halt the entire tree."""
        self._root.halt()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root(self) -> TreeNode:
        return self._root

    @property
    def blackboard(self) -> Blackboard:
        return self._blackboard

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ------------------------------------------------------------------
    # Factory constructor (from XML)
    # ------------------------------------------------------------------

    @classmethod
    def from_xml(
        cls,
        xml_path: str,
        tree_id: Optional[str] = None,
        blackboard: Optional[Blackboard] = None,
        tracer: Optional[ExecutionTracer] = None,
        hz: Optional[float] = None,
    ) -> "BehaviorTreeEngine":
        from bteng.xml_parser.parser import XMLTreeParser
        from bteng.factory.factory import NodeFactory

        bb = blackboard or Blackboard.create()
        parser = XMLTreeParser(NodeFactory.get_instance())
        root = parser.parse_file(xml_path, tree_id=tree_id, blackboard=bb)
        return cls(root, blackboard=bb, tracer=tracer, hz=hz)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inject_tracer(self, node: TreeNode, tracer: ExecutionTracer) -> None:
        node._tracer = tracer
        for child in node.get_children():
            self._inject_tracer(child, tracer)

    def _inject_blackboard(self, node: TreeNode, blackboard: Blackboard) -> None:
        """Give the blackboard to every node that does not already have one.

        A node constructed with its own NodeConfig(blackboard=...) keeps it --
        the same rule the XML parser and TreeExecutor injectors follow.
        """
        if node._config.blackboard is None:
            node._config.blackboard = blackboard
        for child in node.get_children():
            self._inject_blackboard(child, blackboard)
