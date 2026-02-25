"""Admin endpoints backing Pyxel dashboard controls."""

from __future__ import annotations

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
