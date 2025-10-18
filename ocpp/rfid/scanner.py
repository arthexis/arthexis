from django.db import IntegrityError

from core.models import RFID

from .background_reader import get_next_tag, is_configured, start, stop
from .irq_wiring_check import check_irq_pin
from .reader import toggle_deep_read
from .utils import convert_endianness_value, normalize_endianness


def scan_sources(request=None, *, endianness: str | None = None):
    """Read the next RFID tag from the local scanner."""
    if not is_configured():
        return {"rfid": None, "label_id": None}
    result = get_next_tag()
    if not result:
        return {"rfid": None, "label_id": None}
    if result.get("error"):
        return result

    response = dict(result)
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
    if not is_configured():
        return {"error": "no scanner available"}
    enabled = toggle_deep_read()
    status = "deep read enabled" if enabled else "deep read disabled"
    response: dict[str, object] = {"status": status, "enabled": enabled}
    if enabled:
        tag = get_next_tag()
        if tag is not None:
            response["scan"] = tag
    return response
