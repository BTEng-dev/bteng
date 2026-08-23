"""Tree — named, versioned, runtime-modifiable behavior tree instance."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from bteng.core.node import NodeID, NodeStatus, NodeType, PortDefinition, TreeNode
from bteng.blackboard.blackboard import Blackboard

logger = logging.getLogger(__name__)


# ── TreeMetadata ──────────────────────────────────────────────────────────────

@dataclass
class TreeMetadata:
    """Versioned identity and interface description for a tree."""
    id:              str = ""
    description:     str = ""
    version:         str = "1.0.0"
    dependencies:    List[str]          = field(default_factory=list)
    interface_ports: List[PortDefinition] = field(default_factory=list)


# ── TreeModification ──────────────────────────────────────────────────────────

class ModificationType(Enum):
    INSERT_CHILD    = "insert_child"     # add new_node as child of target at index
    REMOVE_CHILD    = "remove_child"     # remove child at index from target
    REPLACE_NODE    = "replace_node"     # replace node identified by target_uid
    HOT_SWAP_SUBTREE = "hot_swap_subtree"  # replace entire subtree at target_uid


@dataclass
class TreeModification:
    """Describes a single structural change to be applied between ticks.

    All modifications go through the pending queue rather than being
    applied directly, so the running executor thread never sees an
    inconsistent state.
    """
    type:        ModificationType
    target_uid:  NodeID
    child_index: int                 = 0
    new_node:    Optional[TreeNode]  = None


#: Modification types that are meaningless without a replacement node.  Letting
#: new_node=None through installs None into the tree, and the *next* tick dies
#: with an AttributeError deep inside the executor instead of at the call site.
_REQUIRES_NEW_NODE = frozenset({
    ModificationType.INSERT_CHILD,
    ModificationType.REPLACE_NODE,
    ModificationType.HOT_SWAP_SUBTREE,
})


# ── Tree ──────────────────────────────────────────────────────────────────────

class Tree:
    """A named, versioned, runtime-modifiable behavior tree instance.

    A Tree owns:
      - Its root TreeNode (the entire node graph is reachable from here).
      - A Blackboard scoped to this tree's level.
      - Metadata (id, version, description, interface ports).
      - A thread-safe modification queue for live structural changes.

    RUNTIME MODIFICATION
    --------------------
    Changes are queued via queue_modification() and applied atomically
    between tick cycles by apply_pending_modifications(), which the
    executor calls at the top of each tick.  A running node is never
    interrupted mid-tick by a structural change.

    Usage::

        tree = Tree(TreeMetadata(id="main"), root_node, blackboard)
        tree.tick_once()
        tree.queue_modification(TreeModification(
            type=ModificationType.REPLACE_NODE,
            target_uid=old_node.uid,
            new_node=new_node,
        ))
    """

    def __init__(
        self,
        metadata:   TreeMetadata,
        root:       TreeNode,
        blackboard: Optional[Blackboard] = None,
    ) -> None:
        self._meta       = metadata
        self._root       = root
        # A private Blackboard, never Blackboard.create(): create() memoises by
        # name, so every tree built without a tree_id would otherwise share one
        # "__tree__" blackboard and inherit the previous tree's keys.
        self._blackboard = blackboard or Blackboard(
            scope_name=metadata.id or "__tree__"
        )
        self._lock       = threading.RLock()
        self._pending_mods: List[TreeModification] = []
        self._pending_lock  = threading.Lock()

    # ── Execution ─────────────────────────────────────────────────────────────

    def tick_once(self) -> NodeStatus:
        """Apply pending modifications and tick the root node once."""
        self.apply_pending_modifications()
        with self._lock:
            return self._root.execute_tick()

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self) -> None:
        """Validate all node port configurations against their declared ports.

        Raises TreeValidationError listing every misconfigured port found.
        Nodes that declare no provided_ports() are skipped.
        """
        from bteng.core.validation import TreeValidationError, validate_tree
        errors = validate_tree(self._root)
        if errors:
            raise TreeValidationError(errors)

    # ── Node lookup ───────────────────────────────────────────────────────────

    def find_node(self, uid: NodeID) -> Optional[TreeNode]:
        """DFS search for a node by uid. Returns None if not found."""
        return self._find_by_uid(self._root, uid)

    def find_node_by_name(self, name: str) -> Optional[TreeNode]:
        """DFS search for a node by name. Returns the first match."""
        return self._find_by_name(self._root, name)

    def find_nodes_by_type(self, node_type: NodeType) -> List[TreeNode]:
        """Return all nodes of the given NodeType (DFS)."""
        results: List[TreeNode] = []
        self._collect_by_type(self._root, node_type, results)
        return results

    # ── Runtime modification ──────────────────────────────────────────────────

    def queue_modification(self, mod: TreeModification) -> None:
        """Queue a structural modification to be applied between ticks.

        Thread-safe.  Applied at the start of the next tick cycle via
        apply_pending_modifications().

        Raises ValueError if the modification could not possibly work — a
        missing target_uid, or a missing new_node for a type that installs one.
        Rejecting here points at the caller; accepting it would install None in
        the tree and blow up one tick later inside the executor.
        """
        if not isinstance(mod, TreeModification):
            raise TypeError(
                f"queue_modification() expects a TreeModification, got {type(mod).__name__}"
            )
        if not mod.target_uid:
            raise ValueError(
                f"TreeModification({mod.type.value}) requires a target_uid"
            )
        if mod.type in _REQUIRES_NEW_NODE and mod.new_node is None:
            raise ValueError(
                f"TreeModification({mod.type.value}) requires new_node, got None"
            )
        with self._pending_lock:
            self._pending_mods.append(mod)

    def apply_pending_modifications(self) -> None:
        """Apply all queued modifications.  Called by the executor before tick_once().

        Must be called from the executor thread only.
        """
        with self._pending_lock:
            mods = list(self._pending_mods)
            self._pending_mods.clear()

        if not mods:
            return

        with self._lock:
            for mod in mods:
                self._apply_modification(mod)

    def hot_swap_subtree(self, target_uid: NodeID, new_root: TreeNode) -> bool:
        """Immediately replace the subtree rooted at target_uid.

        The replaced node is halted before removal.  If target_uid is the
        root, the tree's root pointer is updated.

        WARNING: Call only from the executor thread or while executor is stopped.
        For live hot-swaps from other threads, use queue_modification() instead.

        Returns False if target_uid is not found.
        """
        if new_root is None:
            raise ValueError("hot_swap_subtree() requires a new_root, got None")
        with self._lock:
            if self._root.uid == target_uid:
                self._root.halt()
                self._root = new_root
                return True
            return self._replace_in_subtree(self._root, target_uid, new_root)

    # ── Bulk lifecycle ────────────────────────────────────────────────────────

    def halt_all(self) -> None:
        """Halt all RUNNING nodes (e.g., when the engine is stopped externally)."""
        with self._lock:
            self._root.halt()

    def reset_all(self) -> None:
        """Reset all nodes to IDLE (e.g., before replaying a recorded trace)."""
        with self._lock:
            self._root.reset_node()

    # ── Visitor ───────────────────────────────────────────────────────────────

    def tip(self) -> Optional[TreeNode]:
        """Return the deepest RUNNING node in the tree, or None.

        None once the tree has settled on SUCCESS or FAILURE — there is no
        active branch left to point at.  Under a Parallel only the first
        RUNNING branch is reported; use Inspector.running_nodes() for all.
        """
        with self._lock:
            return self._root.tip()

    def ascii_tree(self, show_status: bool = True) -> str:
        """Render the tree as an ASCII string for debugging."""
        from bteng.introspection.renderer import ascii_tree as _render
        with self._lock:
            return _render(self._root, show_status=show_status)

    def visit(self, visitor: Callable[[TreeNode], None]) -> None:
        """Walk every node depth-first and call visitor on each.

        Uses the tree lock — do not call execute_tick() on any node from
        within the visitor (would deadlock).
        """
        with self._lock:
            self._visit_impl(self._root, visitor)

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def root(self) -> TreeNode:
        return self._root

    @property
    def blackboard(self) -> Blackboard:
        return self._blackboard

    @property
    def metadata(self) -> TreeMetadata:
        return self._meta

    @property
    def id(self) -> str:
        return self._meta.id

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _apply_modification(self, mod: TreeModification) -> None:
        """Apply one queued modification, or log why it did nothing.

        Every branch that changes a parent's child list halts that parent
        afterwards.  Control nodes cache a cursor into their children
        (SequenceNode._current_idx, ReactiveSequenceNode._running_child_idx,
        ...); mutating the list underneath the cursor made a Sequence report
        SUCCESS for children it never ticked, made an insert at index 0 re-run
        the previous child and skip the new one, and crashed a
        ReactiveSequence's fast path with IndexError.  halt() restarts that
        branch from a consistent state — restarting is defensible, reporting a
        final result for unticked children is not.
        """
        if mod.type == ModificationType.REPLACE_NODE:
            if self._root.uid == mod.target_uid:
                self._root.halt()
                self._root = mod.new_node
            elif not self._replace_in_subtree(
                self._root, mod.target_uid, mod.new_node
            ):
                self._warn_unmatched(mod)

        elif mod.type == ModificationType.HOT_SWAP_SUBTREE:
            if not self.hot_swap_subtree(mod.target_uid, mod.new_node):
                self._warn_unmatched(mod)

        elif mod.type == ModificationType.INSERT_CHILD:
            parent = self.find_node(mod.target_uid)
            if parent is None:
                self._warn_unmatched(mod)
            elif hasattr(parent, "_children"):
                idx = min(max(mod.child_index, 0), len(parent._children))
                parent._children.insert(idx, mod.new_node)
                parent.halt()
            elif hasattr(parent, "_child"):
                # A decorator holds exactly one child, so "insert" can only mean
                # "replace".  Say so rather than silently doing nothing.
                logger.warning(
                    "Tree(%r): INSERT_CHILD on decorator %r (uid=%s) replaced its "
                    "only child %r — a decorator cannot hold more than one child",
                    self._meta.id, parent.name, mod.target_uid, parent._child.name,
                )
                parent._child.halt()
                parent._child = mod.new_node
                parent.halt()
            else:
                logger.warning(
                    "Tree(%r): INSERT_CHILD target %r (uid=%s) is a leaf and "
                    "cannot take children — modification ignored",
                    self._meta.id, parent.name, mod.target_uid,
                )

        elif mod.type == ModificationType.REMOVE_CHILD:
            parent = self.find_node(mod.target_uid)
            if parent is None:
                self._warn_unmatched(mod)
            elif hasattr(parent, "_children"):
                if 0 <= mod.child_index < len(parent._children):
                    removed = parent._children.pop(mod.child_index)
                    removed.halt()
                    parent.halt()
                else:
                    logger.warning(
                        "Tree(%r): REMOVE_CHILD index %d out of range for %r "
                        "(uid=%s, %d children) — modification ignored",
                        self._meta.id, mod.child_index, parent.name,
                        mod.target_uid, len(parent._children),
                    )
            elif hasattr(parent, "_child"):
                # Removing it would leave the decorator childless and every
                # later tick would die on None._child.  Refuse, loudly.
                logger.warning(
                    "Tree(%r): REMOVE_CHILD refused on decorator %r (uid=%s) — a "
                    "decorator requires exactly one child; use REPLACE_NODE on the "
                    "child, or REPLACE_NODE on the decorator itself to drop it",
                    self._meta.id, parent.name, mod.target_uid,
                )
            else:
                logger.warning(
                    "Tree(%r): REMOVE_CHILD target %r (uid=%s) is a leaf and has "
                    "no children — modification ignored",
                    self._meta.id, parent.name, mod.target_uid,
                )

        else:
            logger.warning(
                "Tree(%r): unknown modification type %r — ignored",
                self._meta.id, mod.type,
            )

    def _warn_unmatched(self, mod: TreeModification) -> None:
        logger.warning(
            "Tree(%r): %s targeted uid=%s, which is not in this tree — "
            "modification ignored",
            self._meta.id, mod.type.value, mod.target_uid,
        )

    def _replace_in_subtree(
        self, parent: TreeNode, target_uid: NodeID, replacement: TreeNode
    ) -> bool:
        if hasattr(parent, "_children"):
            for i, child in enumerate(parent._children):
                if child.uid == target_uid:
                    child.halt()
                    parent._children[i] = replacement
                    return True
                if self._replace_in_subtree(child, target_uid, replacement):
                    return True
        if hasattr(parent, "_child"):
            if parent._child.uid == target_uid:
                parent._child.halt()
                parent._child = replacement
                return True
            return self._replace_in_subtree(parent._child, target_uid, replacement)
        return False

    def _find_by_uid(self, node: TreeNode, uid: NodeID) -> Optional[TreeNode]:
        if node.uid == uid:
            return node
        for child in node.get_children():
            found = self._find_by_uid(child, uid)
            if found:
                return found
        return None

    def _find_by_name(self, node: TreeNode, name: str) -> Optional[TreeNode]:
        if node.name == name:
            return node
        for child in node.get_children():
            found = self._find_by_name(child, name)
            if found:
                return found
        return None

    def _collect_by_type(
        self, node: TreeNode, node_type: NodeType, results: List[TreeNode]
    ) -> None:
        if node.node_type == node_type:
            results.append(node)
        for child in node.get_children():
            self._collect_by_type(child, node_type, results)

    def _visit_impl(self, node: TreeNode, fn: Callable[[TreeNode], None]) -> None:
        fn(node)
        for child in node.get_children():
            self._visit_impl(child, fn)

    def __repr__(self) -> str:
        return f"Tree(id={self._meta.id!r}, root={self._root!r})"


# ── TreeRegistry ──────────────────────────────────────────────────────────────

class TreeRegistry:
    """Named collection of trees.

    Used by the XML parser to resolve <SubTree ID="..."/> references and by
    the engine for hot-swap operations.  Thread-safe.
    """

    def __init__(self) -> None:
        self._trees: Dict[str, Tree] = {}
        self._lock   = threading.RLock()

    def register_tree(self, tree: Tree) -> None:
        with self._lock:
            self._trees[tree.id] = tree

    def get(self, tree_id: str) -> Optional[Tree]:
        with self._lock:
            return self._trees.get(tree_id)

    def has(self, tree_id: str) -> bool:
        with self._lock:
            return tree_id in self._trees

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._trees.keys())

    def all_trees(self) -> List[Tree]:
        with self._lock:
            return list(self._trees.values())

    def __repr__(self) -> str:
        return f"TreeRegistry(ids={self.ids()})"
