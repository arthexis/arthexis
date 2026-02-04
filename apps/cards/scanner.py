import os
import time

from django.db import IntegrityError
from django.utils import timezone

from apps.cards.models import RFID, RFIDAttempt

from .background_reader import get_next_tag, is_configured, start, stop
from .irq_wiring_check import check_irq_pin
from .reader import toggle_deep_read
from .utils import convert_endianness_value, normalize_endianness
from .rfid_service import deep_read_via_service


RECENT_SCAN_WINDOW_SECONDS = float(
    os.environ.get("RFID_SCAN_RECENT_WINDOW_SECONDS", "10")
)
SCANNER_SOURCES = {
    RFIDAttempt.Source.SERVICE,
    RFIDAttempt.Source.BROWSER,
    RFIDAttempt.Source.CAMERA,
    RFIDAttempt.Source.ON_DEMAND,
}


def _normalize_scan_response(
    result: dict, *, endianness: str | None = None, service_mode: str
) -> dict:
    response = dict(result)
    response["service_mode"] = service_mode
    stored_endianness = normalize_endianness(response.get("endianness"))
    response["endianness"] = stored_endianness
    requested_endianness = (
        normalize_endianness(endianness)
        if endianness is not None
        else stored_endianness
    )

    rfid_value = response.get("rfid")
    if rfid_value:
        normalized_value = str(rfid_value).upper()
        if requested_endianness != stored_endianness:
            converted = convert_endianness_value(
                normalized_value,
                from_endianness=stored_endianness,
                to_endianness=requested_endianness,
            )
            response["rfid"] = converted
            response["endianness"] = requested_endianness
            if response.get("created") and response.get("label_id"):
                tag = RFID.objects.filter(pk=response["label_id"]).first()
                if tag:
                    tag.rfid = converted
                    tag.endianness = requested_endianness
                    try:
                        tag.save(update_fields=["rfid", "endianness"])
                    except IntegrityError:
                        response["rfid"] = normalized_value
                        response["endianness"] = stored_endianness
        else:
            response["rfid"] = normalized_value
    else:
        response["rfid"] = None

    return response


def _service_mode_for_source(source: str) -> str:
    return "service" if source == RFIDAttempt.Source.SERVICE else "on-demand"


def build_attempt_response(
    attempt: RFIDAttempt, *, endianness: str | None = None
) -> dict:
    payload = dict(attempt.payload or {})
    payload.setdefault("rfid", attempt.rfid)
    if attempt.label_id:
        payload.setdefault("label_id", attempt.label_id)
    if attempt.allowed is not None:
        payload.setdefault("allowed", attempt.allowed)
    service_mode = payload.get("service_mode") or _service_mode_for_source(
        attempt.source
    )
    normalized = _normalize_scan_response(
        payload, endianness=endianness, service_mode=service_mode
    )
    normalized["attempt_id"] = attempt.pk
    return normalized


def poll_scan_attempt(
    *,
    after_id: int | None = None,
    endianness: str | None = None,
    sources: set[str] | None = None,
) -> dict:
    sources = sources or SCANNER_SOURCES
    queryset = RFIDAttempt.objects.filter(source__in=sources)
    if after_id:
        attempt = queryset.filter(pk__gt=after_id).order_by("pk").first()
        if attempt:
            return build_attempt_response(attempt, endianness=endianness)
        latest_id = queryset.order_by("-pk").values_list("pk", flat=True).first()
        return {"rfid": None, "service_mode": "service", "last_id": latest_id}
    latest = queryset.order_by("-pk").first()
    if latest:
        age_seconds = (timezone.now() - latest.attempted_at).total_seconds()
        if age_seconds <= RECENT_SCAN_WINDOW_SECONDS:
            return build_attempt_response(latest, endianness=endianness)
    latest_id = latest.pk if latest else None
    return {"rfid": None, "service_mode": "service", "last_id": latest_id}


def record_scan_attempt(
    result: dict,
    *,
    source: str,
    status: str | None = None,
    authenticated: bool | None = None,
) -> RFIDAttempt | None:
    return RFIDAttempt.record_attempt(
        result,
        source=source,
        status=status,
        authenticated=authenticated,
    )


def scan_sources(
    request=None, *, endianness: str | None = None, timeout: float | None = None
):
    """Read the next RFID tag from the local scanner."""
    start_time = time.monotonic()
    service_mode = "on-demand"
    start()
    if not is_configured():
        return {"rfid": None, "label_id": None, "service_mode": service_mode}
    remaining_timeout = 0.0
    if timeout is not None:
        elapsed = time.monotonic() - start_time
        remaining_timeout = max(0.0, timeout - elapsed)
    result = get_next_tag(timeout=remaining_timeout)
    if not result:
        return {"rfid": None, "label_id": None, "service_mode": service_mode}
    if result.get("error"):
        result["service_mode"] = service_mode
        return result

    normalized = _normalize_scan_response(
        result, endianness=endianness, service_mode=service_mode
    )
    attempt = record_scan_attempt(
        normalized, source=RFIDAttempt.Source.ON_DEMAND, status=RFIDAttempt.Status.SCANNED
    )
    if attempt:
        normalized["attempt_id"] = attempt.pk
    return normalized


def restart_sources():
    """Restart the local RFID scanner."""
    if not is_configured():
        return {"error": "no scanner available"}
    try:
        stop()
        start()
        test = get_next_tag()
        if test is not None and not test.get("error"):
            return {"status": "restarted"}
    except Exception:
        pass
    return {"error": "no scanner available"}


def test_sources():
    """Check the local RFID scanner for availability."""
    if not is_configured():
        return {"error": "no scanner available"}
    return check_irq_pin()


def enable_deep_read_mode(duration: float = 60) -> dict:
    """Toggle the RFID reader deep read mode and report the new state."""
    response = deep_read_via_service()
    if response is not None:
        response.setdefault("service_mode", "service")
        return response

    start()
    if not is_configured():
        return {"error": "no scanner available", "service_mode": "on-demand"}
    enabled = toggle_deep_read()
    status = "deep read enabled" if enabled else "deep read disabled"
    response: dict[str, object] = {
        "status": status,
        "enabled": enabled,
        "service_mode": "on-demand",
    }
    if enabled:
        tag = get_next_tag()
        if tag is not None:
            response["scan"] = tag
    return response
