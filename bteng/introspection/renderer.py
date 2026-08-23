"""ASCII tree renderer for debugging."""
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from bteng.core.node import TreeNode

_STATUS_CHAR = {
    "SUCCESS": "✓",
    "FAILURE": "✗",
    "RUNNING": "→",
    "IDLE":    "○",
}


def ascii_tree(node: "TreeNode", show_status: bool = True) -> str:
    """Render a node and its descendants as an indented ASCII string.

    Example output::

        Sequence                     [RUNNING]
        ├── CheckBattery             [SUCCESS]
        └── ReactiveSequence         [RUNNING]
            ├── ObstacleCheck        [SUCCESS]
            └── MoveToGoal           [RUNNING]  moving to (1.2, 3.4)

    Args:
        node: Root of the subtree to render.
        show_status: Include status and feedback_message on each line.
    """
    lines: List[str] = [_label(node, show_status)]
    _render_children(node, prefix="", lines=lines, show_status=show_status)
    return "\n".join(lines)


def print_tree(node: "TreeNode", show_status: bool = True) -> None:
    """Print ascii_tree() to stdout."""
    print(ascii_tree(node, show_status=show_status))


# ── Internal ──────────────────────────────────────────────────────────────────

def _render_children(
    node: "TreeNode",
    prefix: str,
    lines: List[str],
    show_status: bool,
) -> None:
    children = node.get_children()
    for i, child in enumerate(children):
        is_last = i == len(children) - 1
        connector  = "└── " if is_last else "├── "
        extension  = "    " if is_last else "│   "
        lines.append(f"{prefix}{connector}{_label(child, show_status)}")
        _render_children(child, prefix + extension, lines, show_status)


def _label(node: "TreeNode", show_status: bool) -> str:
    name = f"{node.name:<30}"
    if not show_status:
        return name.rstrip()
    status_val = node.status.value
    symbol = _STATUS_CHAR.get(status_val, "?")
    label = f"{name} {symbol} [{status_val}]"
    msg = getattr(node, "_feedback_message", "")
    if msg:
        label += f"  {msg}"
    return label
