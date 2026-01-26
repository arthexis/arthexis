from __future__ import annotations

from apps.core.auto_upgrade import append_auto_upgrade_log
from .auto_upgrade import (
    AutoUpgradeRepositoryState,
    AutoUpgradeMode,
    _canary_gate,
    _ci_status_for_revision,
    _read_auto_upgrade_failure_count,
    check_github_updates,
    legacy_check_github_updates,
)
from .heartbeat import heartbeat, legacy_heartbeat, legacy_module_heartbeat
from .release_checks import (
    SEVERITY_CRITICAL,
    SEVERITY_LOW,
    SEVERITY_NORMAL,
    _get_package_release_model,
    _latest_release,
    _resolve_release_severity,
    execute_scheduled_release,
    legacy_run_scheduled_release,
    run_scheduled_release,
)
from .system_health import (
    legacy_poll_emails,
    legacy_run_client_report_schedule,
    legacy_verify_auto_upgrade_health,
    poll_emails,
    run_client_report_schedule,
    verify_auto_upgrade_health,
)
from .utils import (
    _current_revision,
    _extract_error_output,
    _is_network_failure,
    _project_base_dir,
    _read_process_cmdline,
    _read_process_start_time,
)

__all__ = [
    "_ci_status_for_revision",
    "_canary_gate",
    "_current_revision",
    "_extract_error_output",
    "_get_package_release_model",
    "_is_network_failure",
    "_latest_release",
    "_project_base_dir",
    "_read_auto_upgrade_failure_count",
    "_read_process_cmdline",
    "_read_process_start_time",
    "_resolve_release_severity",
    "AutoUpgradeRepositoryState",
    "AutoUpgradeMode",
    "append_auto_upgrade_log",
    "SEVERITY_CRITICAL",
    "SEVERITY_LOW",
    "SEVERITY_NORMAL",
    "check_github_updates",
    "execute_scheduled_release",
    "heartbeat",
    "legacy_check_github_updates",
    "legacy_heartbeat",
    "legacy_module_heartbeat",
    "legacy_poll_emails",
    "legacy_run_client_report_schedule",
    "legacy_run_scheduled_release",
    "legacy_verify_auto_upgrade_health",
    "poll_emails",
    "run_client_report_schedule",
    "run_scheduled_release",
    "verify_auto_upgrade_health",
]
