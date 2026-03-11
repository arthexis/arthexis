"""Node feature detection callbacks for shortcut listener support."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from django.conf import settings

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry

from .constants import SHORTCUT_LISTENER_NODE_FEATURE_SLUG

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


def _shortcut_listener_available() -> bool:
    """Return whether shortcut listener runtime appears available."""

    env_flag = os.environ.get("ARTHEXIS_SHORTCUT_LISTENER_AVAILABLE", "").strip().lower()
    if env_flag in {"1", "true", "yes", "on"}:
        return True
    if env_flag in {"0", "false", "no", "off"}:
        return False
    return bool(shutil.which("shortcut-listener") or shutil.which("xbindkeys"))


def check_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Return node feature availability for shortcut-listener."""

    del node, base_dir, base_path
    if slug != SHORTCUT_LISTENER_NODE_FEATURE_SLUG:
        return None
    return _shortcut_listener_available()


def setup_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Use the same check callback during setup invocation."""

    resolved_base_dir = base_dir or Path(settings.BASE_DIR)
    resolved_base_path = base_path or node.get_base_path()
    return check_node_feature(
        slug,
        node=node,
        base_dir=resolved_base_dir,
        base_path=resolved_base_path,
    )


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register shortcut-listener detection hooks."""

    registry.register(
        SHORTCUT_LISTENER_NODE_FEATURE_SLUG,
        check=check_node_feature,
        setup=setup_node_feature,
    )
