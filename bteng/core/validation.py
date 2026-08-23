"""Tree-construction-time port validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from bteng.core.node import TreeNode


@dataclass
class PortValidationError:
    """One misconfigured port on one node."""
    node_name: str
    node_uid:  str
    node_type: str
    port_name: str
    message:   str


class TreeValidationError(ValueError):
    """Raised when one or more port validation errors are found in a tree.

    ``errors`` contains the full list so callers can inspect or log them
    individually; the string representation lists all of them at once.
    """

    def __init__(self, errors: List[PortValidationError]) -> None:
        self.errors = errors
        lines = [
            f"  [{e.node_type}] '{e.node_name}' / port '{e.port_name}': {e.message}"
            for e in errors
        ]
        super().__init__("Tree port validation failed:\n" + "\n".join(lines))


def validate_node(node: "TreeNode") -> List[PortValidationError]:
    """Validate one node's NodeConfig against its declared provided_ports().

    Nodes that return an empty list from provided_ports() are skipped —
    they have opted out of port checking (e.g. lambda-based FunctionAction).

    Rules checked:
    - Every name in config.input_ports must appear in provided_ports().
    - Every name in config.output_ports must appear in provided_ports().
    - Every required input port (declared, no default) must have a
      blackboard mapping in config.input_ports or a static value in
      config.params.
    """
    ports = node.provided_ports()
    if not ports:
        return []

    declared = {p.name: p for p in ports}
    cfg      = node._config
    node_type = type(node).__name__
    errors: List[PortValidationError] = []

    def _err(port_name: str, msg: str) -> PortValidationError:
        return PortValidationError(
            node_name=node.name,
            node_uid=node.uid,
            node_type=node_type,
            port_name=port_name,
            message=msg,
        )

    for port_name in cfg.input_ports:
        if port_name not in declared:
            errors.append(_err(
                port_name,
                "mapped as input but not declared in provided_ports()",
            ))

    for port_name in cfg.output_ports:
        if port_name not in declared:
            errors.append(_err(
                port_name,
                "mapped as output but not declared in provided_ports()",
            ))

    for p in ports:
        if p.is_input() and p.default is None:
            if p.name not in cfg.input_ports and p.name not in cfg.params:
                errors.append(_err(
                    p.name,
                    "required input port has no blackboard mapping or static value",
                ))

    return errors


def validate_tree(root: "TreeNode") -> List[PortValidationError]:
    """Walk the full tree and collect all port validation errors.

    Uses an iterative DFS so deep trees don't overflow the call stack.
    """
    errors: List[PortValidationError] = []
    stack = [root]
    while stack:
        node = stack.pop()
        errors.extend(validate_node(node))
        stack.extend(node.get_children())
    return errors
