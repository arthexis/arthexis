"""Views for Raspberry Pi Connect integration app."""

from __future__ import annotations

import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.rpiconnect.services.ingestion_service import IngestionService, IngestionServiceError


def health(request: HttpRequest) -> HttpResponse:
    """Return a minimal app health response."""

    return HttpResponse("ok")


@csrf_exempt
def ingestion_events(request: HttpRequest) -> HttpResponse:
    """Ingest authenticated update/deployment events from external systems."""

    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    expected_token = str(getattr(settings, "RPICONNECT_INGESTION_TOKEN", "") or "")
    if not expected_token:
        return HttpResponse("Ingestion token not configured", status=500)

    header = str(request.headers.get("Authorization", "") or "")
    token = header.removeprefix("Bearer ").strip()
    if token != expected_token:
        return HttpResponse("Unauthorized", status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, ValueError):
        payload = {}

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "invalid payload"}, status=400)

    service = IngestionService()
    try:
        event = service.ingest_event(payload)
    except (IngestionServiceError, ValidationError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse({"event_id": event.event_id, "id": event.pk}, status=202)
