from .background_reader import get_next_tag, is_configured, start, stop
from .irq_wiring_check import check_irq_pin
from .reader import toggle_deep_read


def scan_sources(request=None):
    """Read the next RFID tag from the local scanner."""
    if not is_configured():
        return {"rfid": None, "label_id": None}
    result = get_next_tag()
    if result and (result.get("rfid") or result.get("error")):
        return result
    return {"rfid": None, "label_id": None}


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
    return {"status": status, "enabled": enabled}
