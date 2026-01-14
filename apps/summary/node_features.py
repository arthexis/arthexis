from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from apps.screens.startup_notifications import lcd_feature_enabled_for_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


CELERY_LOCK_NAME = "celery.lck"


def _celery_lock_enabled(base_dir: Path, base_path: Path) -> bool:
    lock_dirs = [base_path / ".locks", base_dir / ".locks"]
    for lock_dir in lock_dirs:
        try:
            if (lock_dir / CELERY_LOCK_NAME).exists():
                return True
        except OSError:
            continue
    return False


def check_node_feature(slug: str, *, node: "Node") -> bool | None:
    if slug != "llm-summary":
        return None

    base_dir = Path(settings.BASE_DIR)
    base_path = node.get_base_path()
    if not lcd_feature_enabled_for_paths(base_dir, base_path):
        return False
    return _celery_lock_enabled(base_dir, base_path)


def setup_node_feature(slug: str, *, node: "Node") -> bool | None:
    if slug != "llm-summary":
        return None
    return check_node_feature(slug, node=node)


def get_llm_summary_prereq_state(
    *, base_dir: Path, base_path: Path
) -> dict[str, bool]:
    return {
        "lcd_enabled": lcd_feature_enabled_for_paths(base_dir, base_path),
        "celery_enabled": _celery_lock_enabled(base_dir, base_path),
    }


__all__ = [
    "check_node_feature",
    "get_llm_summary_prereq_state",
    "setup_node_feature",
]
