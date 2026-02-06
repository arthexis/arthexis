from __future__ import annotations

from apps.core.auto_upgrade import append_auto_upgrade_log
from utils.revision import get_revision

from .locks import AUTO_UPGRADE_SKIP_LOCK_NAME, _read_auto_upgrade_failure_count
from .tasks import (
    AutoUpgradeMode,
    AutoUpgradeRepositoryState,
    SEVERITY_CRITICAL,
    SEVERITY_LOW,
    SEVERITY_NORMAL,
    _broadcast_upgrade_start_message,
    _canary_gate,
    _ci_status_for_revision,
    _current_revision,
    _handle_failed_health_check,
    _project_base_dir,
    _resolve_auto_upgrade_change_tag,
    _send_auto_upgrade_check_message,
    check_github_updates,
    verify_auto_upgrade_health,
)

__all__ = [
    "_broadcast_upgrade_start_message",
    "_canary_gate",
    "_ci_status_for_revision",
    "_current_revision",
    "_handle_failed_health_check",
    "_project_base_dir",
    "_resolve_auto_upgrade_change_tag",
    "_read_auto_upgrade_failure_count",
    "_send_auto_upgrade_check_message",
    "append_auto_upgrade_log",
    "AutoUpgradeMode",
    "AutoUpgradeRepositoryState",
    "AUTO_UPGRADE_SKIP_LOCK_NAME",
    "check_github_updates",
    "get_revision",
    "SEVERITY_CRITICAL",
    "SEVERITY_LOW",
    "SEVERITY_NORMAL",
    "verify_auto_upgrade_health",
]
