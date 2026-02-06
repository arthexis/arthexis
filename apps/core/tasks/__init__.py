from __future__ import annotations

from django.utils import timezone

from .auto_upgrade import (
    AutoUpgradeMode,
    AutoUpgradeRepositoryState,
    SEVERITY_CRITICAL,
    SEVERITY_LOW,
    SEVERITY_NORMAL,
    _broadcast_upgrade_start_message,
    _canary_gate,
    _ci_status_for_revision,
    _current_revision,
    _project_base_dir,
    _resolve_auto_upgrade_change_tag,
    _read_auto_upgrade_failure_count,
    _send_auto_upgrade_check_message,
    append_auto_upgrade_log,
    check_github_updates,
    verify_auto_upgrade_health,
)
from .heartbeat import heartbeat, legacy_heartbeat
from .maintenance import (
    execute_scheduled_release,
    poll_emails,
    run_client_report_schedule,
    run_scheduled_release,
)
from .migrations import _is_migration_server_running
from .system_ops import _read_process_cmdline, _read_process_start_time

__all__ = [
    "_broadcast_upgrade_start_message",
    "_canary_gate",
    "_ci_status_for_revision",
    "_current_revision",
    "_is_migration_server_running",
    "_project_base_dir",
    "_resolve_auto_upgrade_change_tag",
    "_read_auto_upgrade_failure_count",
    "_read_process_cmdline",
    "_read_process_start_time",
    "_send_auto_upgrade_check_message",
    "append_auto_upgrade_log",
    "AutoUpgradeMode",
    "AutoUpgradeRepositoryState",
    "check_github_updates",
    "execute_scheduled_release",
    "heartbeat",
    "legacy_heartbeat",
    "poll_emails",
    "run_client_report_schedule",
    "run_scheduled_release",
    "SEVERITY_CRITICAL",
    "SEVERITY_LOW",
    "SEVERITY_NORMAL",
    "timezone",
    "verify_auto_upgrade_health",
]
