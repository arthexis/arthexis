from .background_reader import get_next_tag, start, stop
from .irq_wiring_check import check_irq_pin


def scan_sources():
    """Read the next RFID tag from the local scanner."""
    result = get_next_tag()
    if result and result.get("rfid"):
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
