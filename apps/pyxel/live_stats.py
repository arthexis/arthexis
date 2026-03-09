"""Utilities for launching and gating the admin Pyxel live-stats viewport."""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from apps.core.ui import build_graphical_subprocess_env


class PyxelLiveStatsLaunchError(RuntimeError):
    """Raised when the Pyxel live-stats process cannot be started."""


@dataclass(frozen=True)
class SuiteStats:
    """Snapshot of top-level suite metrics displayed by the Pyxel overlay."""

    users: int
    active_sessions: int
    installed_apps: int
    registered_models: int
    timestamp: str


def _normalize_ip(value: str | None) -> str:
    """Return a normalized IP string or an empty string when invalid."""

    if not value:
        return ""
    candidate = value.strip().strip("[]")
    if not candidate:
        return ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


def request_client_ip(request) -> str:
    """Resolve the request client IP from direct socket metadata only."""

    return _normalize_ip(request.META.get("REMOTE_ADDR", ""))


def local_ip_addresses(*, include_loopback: bool = True) -> set[str]:
    """Collect local host interface IP addresses for local-request checks."""

    addresses: set[str] = set()

    host_candidates = {socket.gethostname(), socket.getfqdn(), "localhost"}
    for host in host_candidates:
        try:
            _, _, raw_addresses = socket.gethostbyname_ex(host)
        except socket.gaierror:
            continue
        for value in raw_addresses:
            normalized = _normalize_ip(value)
            if normalized:
                addresses.add(normalized)

    if include_loopback:
        addresses.update({"127.0.0.1", "::1"})

    return addresses


def is_local_request(request) -> bool:
    """Return ``True`` when the client IP matches this server's local interfaces."""

    client_ip = request_client_ip(request)
    if not client_ip:
        return False
    return client_ip in local_ip_addresses(include_loopback=True)


def launch_live_stats_subprocess() -> subprocess.Popen:
    """Start the detached management command that opens the live-stats Pyxel window.

    The process is expected to keep running while the Pyxel viewport is open. If it
    exits immediately, we treat that as a launch failure and surface the captured
    stderr output to the admin user.
    """

    command = [
        sys.executable,
        "manage.py",
        "live_stats_viewport",
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            env=build_graphical_subprocess_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        startup_deadline = time.monotonic() + 1.0
        while time.monotonic() < startup_deadline:
            if process.poll() is not None:
                _, stderr_output = process.communicate()
                error_detail = (stderr_output or "").strip()
                if error_detail:
                    raise PyxelLiveStatsLaunchError(
                        f"Unable to launch Pyxel live stats window: {error_detail}"
                    )
                raise PyxelLiveStatsLaunchError(
                    "Unable to launch Pyxel live stats window: process exited immediately"
                )
            time.sleep(0.05)
        return process
    except OSError as exc:
        raise PyxelLiveStatsLaunchError("Unable to launch Pyxel live stats window") from exc


def collect_suite_stats() -> SuiteStats:
    """Build the current suite metric snapshot for rendering in the Pyxel window."""

    from django.apps import apps as django_apps
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session

    user_model = get_user_model()
    active_sessions = Session.objects.filter(expire_date__gte=timezone.now()).count()
    return SuiteStats(
        users=user_model.objects.count(),
        active_sessions=active_sessions,
        installed_apps=len(settings.INSTALLED_APPS),
        registered_models=len(list(django_apps.get_models())),
        timestamp=timezone.localtime().strftime("%H:%M:%S"),
    )
