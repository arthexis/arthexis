"""Service status reporting helpers for the system UI.

Data flow:
- Resolve configured lifecycle units for the current instance.
- Query systemd for active/enabled state when available.
- Fall back to embedded PID checks when the suite runs in embedded mode.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.core.systemctl import _systemctl_command
from apps.services.lifecycle import build_lifecycle_service_units

from ..filesystem import _pid_file_running, _read_service_mode


def _configured_service_units(base_dir: Path) -> list[dict[str, object]]:
    """Return service units configured for this instance."""

    return build_lifecycle_service_units(base_dir)


def _systemd_unit_status(unit: str, command: list[str] | None = None) -> dict[str, object]:
    """Return the systemd status for a unit, handling missing commands gracefully."""

    command = command if command is not None else _systemctl_command()
    if not command:
        return {
            "status": str(_("Unavailable")),
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
            "status": str(_("Unknown")),
            "enabled": "",
            "missing": False,
        }
    except Exception:
        return {
            "status": str(_("Unknown")),
            "enabled": "",
            "missing": False,
        }

    status_output = (active_result.stdout or active_result.stderr).strip()
    status = status_output or str(_("unknown"))
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


def _embedded_service_status(lock_dir: Path, pid_file: str) -> dict[str, object]:
    """Return synthetic service status when running in embedded mode."""

    running = _pid_file_running(lock_dir / pid_file)
    status_label = _("active (embedded)") if running else _("inactive (embedded)")
    return {
        "status": str(status_label),
        "enabled": str(_("Embedded")),
        "missing": False,
    }


def _build_services_report() -> dict[str, object]:
    """Build the service report payload rendered in the admin panel."""

    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / ".locks"
    configured_units = _configured_service_units(base_dir)
    command = _systemctl_command()
    service_mode = _read_service_mode(lock_dir)
    embedded_mode = service_mode == "embedded"

    services: list[dict[str, object]] = []
    for unit in configured_units:
        if unit.get("configured"):
            pid_file = unit.get("pid_file", "")
            if embedded_mode and pid_file:
                status_info = _embedded_service_status(lock_dir, pid_file)
            else:
                status_info = _systemd_unit_status(unit["unit"], command=command)
        else:
            status_info = {
                "status": str(_("Not configured")),
                "enabled": "",
                "missing": False,
            }
        services.append({**unit, **status_info})

    return {
        "services": services,
        "systemd_available": bool(command),
        "has_services": bool(configured_units),
    }
