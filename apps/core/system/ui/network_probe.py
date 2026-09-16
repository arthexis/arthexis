"""Network and process probing utilities used by system UI reports.

Data flow:
- Determine whether the suite is reachable by first checking for a running
  ``manage.py runserver`` process and extracting its port from command output.
- Probe a prioritized set of localhost ports when process inspection is
  inconclusive.
"""

from __future__ import annotations

from utils.service_probe import detect_runserver_port, probe_admin_login


def _detect_runserver_process() -> tuple[bool, int | None]:
    """Return whether the dev server is running and the port if available."""

    port = detect_runserver_port()
    if port is None:
        return False, None
    return True, port


def _probe_ports(candidates: list[int]) -> tuple[bool, int | None]:
    """Attempt to probe the admin login endpoint on the provided ports."""

    for port in candidates:
        result = probe_admin_login(port, timeout=0.25)
        if result.reachable:
            return True, port
    return False, None


def _port_candidates(default_port: int) -> list[int]:
    """Return a prioritized list of ports to probe for the HTTP service."""

    candidates = [default_port] if 1 <= default_port <= 65535 else []
    for port in (8000, 8888):
        if port not in candidates:
            candidates.append(port)
    return candidates
