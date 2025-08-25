from .reader import read_rfid


def scan_sources():
    """Read the next RFID tag from the local scanner."""
    result = read_rfid()
    if result and result.get("rfid"):
        return result
    return {"rfid": None, "label_id": None}

