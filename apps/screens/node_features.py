from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from .startup_notifications import lcd_feature_enabled_for_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


def check_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Return ``True`` when the LCD screen feature can be enabled."""

    if slug != "lcd-screen":
        return None
    base_dir = Path(settings.BASE_DIR)
    base_path = node.get_base_path()
    return lcd_feature_enabled_for_paths(base_dir, base_path)


def setup_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Allow the LCD feature to manage its own detection lifecycle."""

    if slug != "lcd-screen":
        return None
    return check_node_feature(slug, node=node)


__all__ = ["check_node_feature", "setup_node_feature"]
