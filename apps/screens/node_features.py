from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry

from .startup_notifications import lcd_feature_enabled_for_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


LCD_SCREEN_SLUG = "lcd-screen"


def check_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Return ``True`` when the LCD screen feature can be enabled."""

    if slug != LCD_SCREEN_SLUG:
        return None
    resolved_base_dir = base_dir or Path(settings.BASE_DIR)
    resolved_base_path = base_path or node.get_base_path()
    return lcd_feature_enabled_for_paths(resolved_base_dir, resolved_base_path)


def setup_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Allow the LCD feature to manage its own detection lifecycle."""

    if slug != LCD_SCREEN_SLUG:
        return None
    return check_node_feature(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register screen app feature auto-detection callbacks."""

    registry.register(
        LCD_SCREEN_SLUG,
        check=check_node_feature,
        setup=setup_node_feature,
    )


__all__ = [
    "check_node_feature",
    "register_node_feature_detection",
    "setup_node_feature",
]
