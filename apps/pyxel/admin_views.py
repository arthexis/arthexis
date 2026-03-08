"""Admin endpoints backing Pyxel dashboard controls."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.pyxel.live_stats import (
    PyxelLiveStatsLaunchError,
    is_local_request,
    launch_live_stats_subprocess,
)
from apps.pyxel.models import PyxelViewport


class PyxelViewportLaunchError(RuntimeError):
    """Raised when the Pyxel viewport process cannot be started."""


VIEWPORT_STARTUP_GRACE_SECONDS = 3.0


def has_graphical_display() -> bool:
    """Return ``True`` when the server environment can open GUI windows."""

    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def viewport_opened_message(viewport_name: str) -> str:
    """Build a success message that clarifies where the viewport appears."""

    message = (
        f"Opened Pyxel viewport '{viewport_name}'. "
        "The window opens on the server desktop, not inside this browser."
    )
    release = platform.release().lower()
    if "microsoft" in release or os.environ.get("WSL_DISTRO_NAME"):
        return f"{message} In WSL, it appears in the Linux desktop session (WSLg/X11)."
    return message


@require_POST
def open_live_stats_view(request):
    """Launch the local Pyxel live-stats window for local-only admin requests."""

    if not is_local_request(request):
        raise PermissionDenied("Pyxel live stats is only available from local server addresses")

    try:
        launch_live_stats_subprocess()
    except PyxelLiveStatsLaunchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Opened local Pyxel live stats window")
    return redirect(reverse("admin:index"))


def launch_viewport_subprocess(*, viewport_slug: str | None = None) -> subprocess.Popen:
    """Start the detached command that opens a regular Pyxel viewport window."""

    command = [sys.executable, "manage.py", "viewport"]
    if viewport_slug:
        command.append(viewport_slug)

    try:
        process = subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        startup_deadline = time.monotonic() + VIEWPORT_STARTUP_GRACE_SECONDS
        while time.monotonic() < startup_deadline:
            if process.poll() is not None:
                _, stderr_output = process.communicate()
                error_detail = (stderr_output or "").strip()
                if error_detail:
                    raise PyxelViewportLaunchError(
                        f"Unable to launch Pyxel viewport: {error_detail}"
                    )
                raise PyxelViewportLaunchError(
                    "Unable to launch Pyxel viewport: process exited immediately"
                )
            time.sleep(0.05)
        return process
    except OSError as exc:
        raise PyxelViewportLaunchError("Unable to launch Pyxel viewport") from exc


@require_POST
def open_viewport_view(request, pk: int | None = None):
    """Launch a viewport by primary key or fallback to default/only viewport."""

    if not is_local_request(request):
        raise PermissionDenied("Pyxel viewport is only available from local server addresses")

    if not has_graphical_display():
        messages.error(
            request,
            "Unable to launch Pyxel viewport: no graphical display is configured "
            "for this server process (DISPLAY/WAYLAND_DISPLAY).",
        )
        if pk is None:
            return redirect(reverse("admin:pyxel_pyxelviewport_changelist"))
        return redirect(reverse("admin:pyxel_pyxelviewport_change", args=[pk]))

    target_viewport: PyxelViewport
    if pk is None:
        try:
            target_viewport = PyxelViewport.default_or_only()
        except PyxelViewport.DoesNotExist:
            messages.error(request, "No Pyxel viewport exists to open")
            return redirect(reverse("admin:pyxel_pyxelviewport_changelist"))
        except PyxelViewport.MultipleObjectsReturned as exc:
            messages.error(request, str(exc))
            return redirect(reverse("admin:pyxel_pyxelviewport_changelist"))
    else:
        try:
            target_viewport = PyxelViewport.objects.get(pk=pk)
        except PyxelViewport.DoesNotExist:
            messages.error(request, "Requested Pyxel viewport was not found")
            return redirect(reverse("admin:pyxel_pyxelviewport_changelist"))

    try:
        launch_viewport_subprocess(viewport_slug=target_viewport.slug)
    except PyxelViewportLaunchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, viewport_opened_message(target_viewport.name))

    if pk is None:
        return redirect(reverse("admin:pyxel_pyxelviewport_changelist"))
    return redirect(reverse("admin:pyxel_pyxelviewport_change", args=[pk]))
