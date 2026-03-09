"""Public helpers for system status formatting and reporting.

This module provides a stable, flatter import path for shared helpers that
are consumed across commands, admin actions, and health checks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from .system.ui import (
    STARTUP_REPORT_DEFAULT_LIMIT,
    STARTUP_CLOCK_DRIFT_THRESHOLD,
    SystemField,
    _build_system_fields,
    _gather_info,
    _read_startup_report,
)
from .system.ui.formatting import _format_datetime, _format_timestamp
from .system.ui.network_probe import _build_nginx_report
from .system.ui.services import _build_services_report, _configured_service_units, _systemd_unit_status
from .system.ui.uptime import (
    _build_uptime_report,
    _build_uptime_segments,
    _load_shutdown_periods,
    _suite_offline_period,
    _suite_uptime_details,
    _system_boot_time,
)


def build_nginx_report() -> dict[str, object]:
    """Return nginx expected-vs-actual report metadata."""

    return _build_nginx_report()


def build_services_report() -> dict[str, object]:
    """Return lifecycle services report metadata."""

    return _build_services_report()


def build_system_fields(info: dict[str, object]) -> list[SystemField]:
    """Convert gathered system info into displayable system fields."""

    return _build_system_fields(info)


def build_uptime_report() -> dict[str, object]:
    """Return a combined uptime report payload."""

    return _build_uptime_report()


def build_uptime_segments(*, window_start: datetime, window_end: datetime, shutdown_periods: list[tuple[datetime, datetime]]) -> list[dict[str, object]]:
    """Build alternating online/offline segments in the reporting window."""

    return _build_uptime_segments(
        window_start=window_start,
        window_end=window_end,
        shutdown_periods=shutdown_periods,
    )


def configured_service_units(base_dir: Path) -> list[dict[str, object]]:
    """Return configured lifecycle units for the current instance."""

    return _configured_service_units(base_dir)


def format_datetime(dt: datetime | None) -> str:
    """Return a concise datetime label used by command and admin output."""

    return _format_datetime(dt)


def format_timestamp(dt: datetime | None) -> str:
    """Return a localized timestamp label used by command and health output."""

    return _format_timestamp(dt)


def gather_info(auto_upgrade_next_check: Callable[[], str]) -> dict[str, object]:
    """Gather baseline system metadata used by sigils and admin pages."""

    return _gather_info(auto_upgrade_next_check)


def load_shutdown_periods() -> tuple[list[tuple[datetime, datetime | None]], str | None]:
    """Load shutdown periods from system uptime history."""

    return _load_shutdown_periods()


def read_startup_report(*, limit: int | None = None, base_dir: Path | None = None) -> dict[str, object]:
    """Read startup report entries with optional entry limit and base dir override."""

    return _read_startup_report(limit=limit, base_dir=base_dir)


def suite_offline_period(now: datetime) -> tuple[datetime, datetime] | None:
    """Return synthetic offline window when lock predates current boot."""

    return _suite_offline_period(now)


def suite_uptime_details() -> dict[str, object]:
    """Return suite uptime metadata derived from boot/lock state."""

    return _suite_uptime_details()


def system_boot_time(now: datetime) -> datetime | None:
    """Return system boot time for the supplied reference time."""

    return _system_boot_time(now)


def systemd_unit_status(unit: str, command: list[str] | None = None) -> dict[str, object]:
    """Return systemd active/enabled state for ``unit``."""

    return _systemd_unit_status(unit, command=command)


__all__ = [
    "STARTUP_CLOCK_DRIFT_THRESHOLD",
    "STARTUP_REPORT_DEFAULT_LIMIT",
    "SystemField",
    "build_nginx_report",
    "build_services_report",
    "build_system_fields",
    "build_uptime_report",
    "build_uptime_segments",
    "configured_service_units",
    "format_datetime",
    "format_timestamp",
    "gather_info",
    "load_shutdown_periods",
    "read_startup_report",
    "suite_offline_period",
    "suite_uptime_details",
    "system_boot_time",
    "systemd_unit_status",
]
