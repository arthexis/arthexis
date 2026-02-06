from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import socket
import subprocess

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.core.systemctl import _systemctl_command

from ..filesystem import _configured_backend_port


_RUNSERVER_PORT_PATTERN = re.compile(r":(\d{2,5})(?:\D|$)")
_RUNSERVER_PORT_FLAG_PATTERN = re.compile(r"--port(?:=|\s+)(\d{2,5})", re.IGNORECASE)


def _parse_runserver_port(command_line: str) -> int | None:
    """Extract the HTTP port from a runserver command line."""

    for pattern in (_RUNSERVER_PORT_PATTERN, _RUNSERVER_PORT_FLAG_PATTERN):
        match = pattern.search(command_line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _detect_runserver_process() -> tuple[bool, int | None]:
    """Return whether the dev server is running and the port if available."""

    try:
        result = subprocess.run(
            ["pgrep", "-af", "manage.py runserver"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, None
    except Exception:
        return False, None

    if result.returncode != 0:
        return False, None

    output = result.stdout.strip()
    if not output:
        return False, None

    port = None
    for line in output.splitlines():
        port = _parse_runserver_port(line)
        if port is not None:
            break

    if port is None:
        port = _configured_backend_port(Path(settings.BASE_DIR))

    return True, port


def _probe_ports(candidates: list[int]) -> tuple[bool, int | None]:
    """Attempt to connect to localhost on the provided ports."""

    for port in candidates:
        try:
            with closing(socket.create_connection(("localhost", port), timeout=0.25)):
                return True, port
        except OSError:
            continue
    return False, None


def _port_candidates(default_port: int) -> list[int]:
    """Return a prioritized list of ports to probe for the HTTP service."""

    candidates = [default_port]
    for port in (8000, 8888):
        if port not in candidates:
            candidates.append(port)
    return candidates


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
        )
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
            )
            enabled_state = (enabled_result.stdout or enabled_result.stderr).strip()
        except Exception:
            enabled_state = ""

    return {
        "status": status,
        "enabled": enabled_state,
        "missing": missing,
    }
