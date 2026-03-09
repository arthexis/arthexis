"""Compatibility module for core node models."""

from __future__ import annotations

from .net_message import NetMessage, PendingNetMessage
from .node import Node, User, node_information_updated
from .platform import Platform
from .role import NodeRole, NodeRoleManager, get_terminal_role
from .utils import (
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
