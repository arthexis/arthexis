from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.db.utils import OperationalError, ProgrammingError

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry
from apps.screens.startup_notifications import lcd_feature_enabled_for_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


CELERY_LOCK_NAME = "celery.lck"
LLM_SUMMARY_SLUG = "llm-summary"


def _celery_lock_enabled(base_dir: Path, base_path: Path) -> bool:
    lock_dirs = [base_path / ".locks", base_dir / ".locks"]
    for lock_dir in lock_dirs:
        try:
            if (lock_dir / CELERY_LOCK_NAME).exists():
                return True
        except OSError:
            continue
    return False


def _is_llm_summary_active(*, base_dir: Path, base_path: Path) -> bool:
    """Return whether this node can generate LLM summary values."""

    try:
        from apps.summary.services import get_summary_config
    except ImportError:
        return False

    try:
        config = get_summary_config()
    except (OperationalError, ProgrammingError):
        return False

    return bool(config.is_active)


def check_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Return whether llm-summary can be auto-enabled for ``node``."""

    if slug != LLM_SUMMARY_SLUG:
        return None
    return _is_llm_summary_active(base_dir=base_dir, base_path=base_path)


def setup_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Allow the summary app to own llm-summary auto-detection."""

    if slug != LLM_SUMMARY_SLUG:
        return None
    return check_node_feature(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def get_llm_summary_prereq_state(*, base_dir: Path, base_path: Path) -> dict[str, bool]:
    """Return optional LCD output and Celery scheduling state for summaries."""

    return {
        "lcd_enabled": lcd_feature_enabled_for_paths(base_dir, base_path),
        "celery_enabled": _celery_lock_enabled(base_dir, base_path),
    }


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register summary app feature auto-detection callbacks."""

    registry.register(
        LLM_SUMMARY_SLUG,
        check=check_node_feature,
        setup=setup_node_feature,
    )


__all__ = [
    "check_node_feature",
    "get_llm_summary_prereq_state",
    "register_node_feature_detection",
    "setup_node_feature",
]
