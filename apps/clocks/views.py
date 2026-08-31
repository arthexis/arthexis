from __future__ import annotations

from zoneinfo import ZoneInfo, available_timezones

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import ClockDevice


def public_clock_view(request, slug):
    device = get_object_or_404(
        ClockDevice, public_view_slug=slug, enable_public_view=True
    )

    timezone_name = request.GET.get("tz") or settings.TIME_ZONE
    timezone_choices = sorted(available_timezones())
    if timezone_name not in timezone_choices:
        timezone_name = settings.TIME_ZONE
    tzinfo = ZoneInfo(timezone_name)
    now = timezone.now().astimezone(tzinfo)

    context = {
        "device": device,
        "timezone_name": timezone_name,
        "timezones": timezone_choices,
        "now": now,
    }
    return render(request, "clocks/public_clock.html", context)
