from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from apps.screens.startup_notifications import lcd_feature_enabled_for_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


def check_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Enable the log summary LLM when Celery and LCD are available."""

    if slug != "llm-summary":
        return None

    if not node.is_local or not node.has_feature("celery-queue"):
        return False

    base_dir = Path(settings.BASE_DIR)
    base_path = node.get_base_path()
    return lcd_feature_enabled_for_paths(base_dir, base_path)


def setup_node_feature(slug: str, *, node: "Node") -> bool | None:
    if slug != "llm-summary":
        return None
    return check_node_feature(slug, node=node)


__all__ = ["check_node_feature", "setup_node_feature"]
