from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.db.utils import OperationalError, ProgrammingError

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry
from apps.nodes.roles import node_is_control
from apps.summary.models import LLMSummaryConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


CELERY_LOCK_NAME = "celery.lck"
LLM_SUMMARY_SLUG = "llm-summary"
SUMMARY_CONFIG_SLUG = "log-summary"


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
    """Return whether this node can generate deterministic summary values."""

    try:
        return LLMSummaryConfig.objects.filter(
            slug=SUMMARY_CONFIG_SLUG,
            is_active=True,
        ).exists()
    except (OperationalError, ProgrammingError):
        return False


def check_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Return whether deterministic summary can be auto-enabled for ``node``."""

    if slug != LLM_SUMMARY_SLUG:
        return None
    if not node_is_control(node):
        return False
    return _is_llm_summary_active(base_dir=base_dir, base_path=base_path)


def setup_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Allow the summary app to own deterministic summary auto-detection."""

    if slug != LLM_SUMMARY_SLUG:
        return None
    return check_node_feature(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def get_llm_summary_prereq_state(*, base_dir: Path, base_path: Path) -> dict[str, bool]:
    """Return Celery scheduling state for summaries."""

    return {
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
