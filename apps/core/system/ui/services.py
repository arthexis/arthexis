"""Service status reporting helpers for the system UI.

Data flow:
- Resolve configured lifecycle units for the current instance.
- Query systemd for active/enabled state when available.
- Fall back to embedded PID checks when the suite runs in embedded mode.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict, cast

from django.conf import settings
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from typing_extensions import NotRequired

from apps.core.systemctl import _systemctl_command
from apps.services.lifecycle import build_lifecycle_service_units

from ..filesystem import _pid_file_running, _read_service_mode


class ServiceUnitConfig(TypedDict):
    """Configured lifecycle unit metadata."""

    configured: bool
    label: str
    pid_file: str
    unit: str


class ServiceStatusPayload(TypedDict):
    """Rendered status metadata for a lifecycle service."""

    enabled: str
    missing: bool
    status: str


class ServiceReportEntry(ServiceUnitConfig, ServiceStatusPayload):
    """Combined lifecycle unit row used by the UI report."""


class ServiceReportPayload(TypedDict):
    """Top-level service report returned to system UI."""

    has_services: bool
    services: list[ServiceReportEntry]
    systemd_available: bool


class NginxReportPayload(TypedDict):
    """Comparison payload for managed nginx configuration output."""

    actual_content: str
    actual_error: str
    actual_path: Path
    differs: bool
    expected_content: str
    expected_error: str
    expected_path: Path
    external_websockets: bool
    mode: str
    port: int


class UptimeSegmentPayload(TypedDict):
    """Raw uptime segment built from shutdown windows."""

    duration: timedelta
    end: datetime
    start: datetime
    status: Literal["down", "up"]


class SerializedUptimeSegmentPayload(UptimeSegmentPayload):
    """Segment payload prepared for UI rendering."""

    duration_label: str
    label: str
    width: float


class UptimeDowntimeEventPayload(TypedDict):
    """Condensed downtime event display payload."""

    duration: str
    end: str
    start: str


class UptimeWindowPayload(TypedDict):
    """Window-level uptime summary for a fixed reporting range."""

    downtime_events: list[UptimeDowntimeEventPayload]
    downtime_percent: float
    end: datetime
    label: str
    segments: list[SerializedUptimeSegmentPayload]
    start: datetime
    uptime_percent: float


class SuiteUptimeDetailsPayload(TypedDict):
    """Detailed suite uptime payload from boot/lock metadata."""

    available: bool
    boot_time: datetime | None
    boot_time_label: str
    lock_started_at: NotRequired[datetime]
    uptime: NotRequired[str]


class UptimeReportSuitePayload(TypedDict):
    """Suite uptime subsection embedded in the UI report."""

    available: bool
    boot_time: datetime | None
    boot_time_label: str
    uptime: str


class UptimeReportPayload(TypedDict):
    """Top-level uptime report payload rendered by the UI."""

    error: str | None
    generated_at: datetime
    suite: UptimeReportSuitePayload
    windows: list[UptimeWindowPayload]


def _as_str(value: Promise | str) -> str:
    """Narrow lazy translation values to ``str`` for typed payloads."""

    return cast(str, value)


def _configured_service_units(base_dir: Path) -> list[ServiceUnitConfig]:
    """Return service units configured for this instance."""

    return cast(list[ServiceUnitConfig], build_lifecycle_service_units(base_dir))


def _systemd_unit_status(unit: str, command: list[str] | None = None) -> ServiceStatusPayload:
    """Return the systemd status for a unit, handling missing commands gracefully."""

    command = command if command is not None else _systemctl_command()
    if not command:
        return {
            "status": _as_str(_("Unavailable")),
            "enabled": "",
            "missing": False,
        }

    try:
        active_result = subprocess.run(
            [*command, "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": _as_str(_("Unknown")),
            "enabled": "",
            "missing": False,
        }
    except Exception:
        return {
            "status": _as_str(_("Unknown")),
            "enabled": "",
            "missing": False,
        }

    status_output = (active_result.stdout or active_result.stderr).strip()
    status = status_output or _as_str(_("unknown"))
    missing = active_result.returncode == 4

    enabled_state = ""
    if not missing:
        try:
            enabled_result = subprocess.run(
                [*command, "is-enabled", unit],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.5,
            )
            enabled_state = (enabled_result.stdout or enabled_result.stderr).strip()
        except subprocess.TimeoutExpired:
            enabled_state = ""
        except Exception:
            enabled_state = ""

    return {
        "status": status,
        "enabled": enabled_state,
        "missing": missing,
    }


def _embedded_service_status(lock_dir: Path, pid_file: str) -> ServiceStatusPayload:
    """Return synthetic service status when running in embedded mode."""

    running = _pid_file_running(lock_dir / pid_file)
    status_label = _("active (embedded)") if running else _("inactive (embedded)")
    return {
        "status": _as_str(status_label),
        "enabled": _as_str(_("Embedded")),
        "missing": False,
    }


def _build_services_report() -> ServiceReportPayload:
    """Build the service report payload rendered in the admin panel."""

    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / ".locks"
    configured_units = _configured_service_units(base_dir)
    command = _systemctl_command()
    service_mode = _read_service_mode(lock_dir)
    embedded_mode = service_mode == "embedded"

    services: list[ServiceReportEntry] = []
    for unit in configured_units:
        if unit.get("configured"):
            pid_file = unit.get("pid_file", "")
            if embedded_mode and pid_file:
                status_info = _embedded_service_status(lock_dir, pid_file)
            else:
                status_info = _systemd_unit_status(unit["unit"], command=command)
        else:
            status_info = {
                "status": _as_str(_("Not configured")),
                "enabled": "",
                "missing": False,
            }
        services.append({**unit, **status_info})

    return {
        "services": services,
        "systemd_available": bool(command),
        "has_services": bool(configured_units),
    }
