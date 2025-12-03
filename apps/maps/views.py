from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from apps.ocpp.models import EVCSChargePoint
from apps.pages.utils import landing


def _charger_display_name(charger: EVCSChargePoint) -> str:
    if charger.display_name:
        return charger.display_name
    if charger.location:
        return charger.location.name
    return charger.charger_id


@landing(_("Charge Point Map"))
def charge_point_map(request: HttpRequest) -> HttpResponse:
    """Display a map with EVCS pins filtered by location visibility."""

    charger_qs = (
        EVCSChargePoint.visible_for_user(request.user)
        .select_related("location")
        .filter(
            location__isnull=False,
            location__latitude__isnull=False,
            location__longitude__isnull=False,
        )
    )

    if not request.user.is_authenticated:
        charger_qs = charger_qs.filter(location__is_public=True)

    markers: list[dict[str, Any]] = []
    for charger in charger_qs:
        location = charger.location
        if location is None:
            continue
        markers.append(
            {
                "id": charger.pk or charger.charger_id,
                "name": _charger_display_name(charger),
                "location": location.name,
                "latitude": float(location.latitude),
                "longitude": float(location.longitude),
                "address": ", ".join(
                    filter(
                        None,
                        [
                            location.address_line1,
                            location.city,
                            location.state,
                            location.country,
                        ],
                    )
                ),
            }
        )

    context = {
        "markers": markers,
        "shows_private_locations": request.user.is_authenticated,
    }
    return render(request, "maps/charge_point_map.html", context)
