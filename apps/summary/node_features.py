from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from apps.screens.startup_notifications import lcd_feature_enabled_for_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


CELERY_LOCK_NAME = "celery.lck"


def _celery_lock_enabled(base_dir: Path, base_path: Path) -> bool:
    """Return whether a celery lock file exists in node or project lock dirs."""

    lock_dirs = [base_path / ".locks", base_dir / ".locks"]
    for lock_dir in lock_dirs:
        try:
            if (lock_dir / CELERY_LOCK_NAME).exists():
                return True
        except OSError:
            continue
    return False


def check_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Resolve dynamic node feature state for summary-related slugs."""

    if slug != "llm-summary":
        return None

    return node.has_feature("lcd-screen") and node.has_feature("celery-queue")


def setup_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Use runtime feature checks as setup detection for summary feature."""

    if slug != "llm-summary":
        return None
    return check_node_feature(slug, node=node)


def get_llm_summary_prereq_state(
    *,
    node: "Node | None" = None,
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> dict[str, bool]:
    """Return LLM-summary prerequisites from feature state with lock fallback.

    During very early bootstrap the node/feature tables can be unavailable; in
    that case fall back to lock-file checks when paths are supplied.
    """

    if node is not None:
        try:
            return {
                "lcd_enabled": node.has_feature("lcd-screen"),
                "celery_enabled": node.has_feature("celery-queue"),
            }
        except Exception:
            # Compatibility fallback for bootstrap phases when DB lookups are
            # not yet available.
            pass

    resolved_base_dir = base_dir or Path(settings.BASE_DIR)
    resolved_base_path = base_path or (node.get_base_path() if node else resolved_base_dir)
    return {
        "lcd_enabled": lcd_feature_enabled_for_paths(
            resolved_base_dir, resolved_base_path
        ),
        "celery_enabled": _celery_lock_enabled(
            resolved_base_dir, resolved_base_path
        ),
    }


__all__ = [
    "check_node_feature",
    "get_llm_summary_prereq_state",
    "setup_node_feature",
]
