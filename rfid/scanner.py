from .background_reader import get_next_tag, start, stop
from .irq_wiring_check import check_irq_pin
from accounts.models import RFID


def scan_sources(request=None):
    """Read the next RFID tag from the local scanner."""
    result = get_next_tag()
    if result:
        if request and result.get("label_id"):
            try:
                tag = RFID.objects.get(pk=result["label_id"])
                tag.save(request=request)
                result["reference"] = tag.reference.value if tag.reference else None
            except RFID.DoesNotExist:
                pass
        if result.get("rfid") or result.get("error"):
            return result
    return {"rfid": None, "label_id": None}


def restart_sources():
    """Restart the local RFID scanner."""
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
    return check_irq_pin()
