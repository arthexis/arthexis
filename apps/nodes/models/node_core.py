"""Compatibility module for core node models."""

from __future__ import annotations

from .core.node import (
    NetMessage,
    Node,
    PendingNetMessage,
    User,
    node_information_updated,
)
from .core.platform import Platform
from .core.role import NodeRole, NodeRoleManager, get_terminal_role
from .core.utils import (
    ROLE_ACRONYMS,
    ROLE_RENAMES,
    _format_upgrade_body,
    _upgrade_in_progress,
)

__all__ = [
    "NetMessage",
    "Node",
    "NodeRole",
    "NodeRoleManager",
    "PendingNetMessage",
    "Platform",
    "ROLE_ACRONYMS",
    "ROLE_RENAMES",
    "User",
    "_format_upgrade_body",
    "_upgrade_in_progress",
    "get_terminal_role",
    "node_information_updated",
]
