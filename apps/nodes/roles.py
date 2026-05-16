from __future__ import annotations

CONTROL_NODE_ROLE = "Control"
CONTROL_ONLY_NODE_FEATURE_SLUGS = frozenset({"llm-summary", "usb-inventory"})


def node_has_role(node: object, role_name: str) -> bool:
    """Return whether a node-like object has the named role."""

    role = getattr(node, "role", None)
    return getattr(role, "name", None) == role_name


def node_is_control(node: object) -> bool:
    """Return whether a node-like object is assigned to the Control role."""

    return node_has_role(node, CONTROL_NODE_ROLE)


def node_feature_allowed_for_node(slug: str, node: object) -> bool:
    """Return whether a node feature may be active for a node role."""

    if slug not in CONTROL_ONLY_NODE_FEATURE_SLUGS:
        return True
    return node_is_control(node)


__all__ = [
    "CONTROL_NODE_ROLE",
    "CONTROL_ONLY_NODE_FEATURE_SLUGS",
    "node_feature_allowed_for_node",
    "node_has_role",
    "node_is_control",
]
