import time
from accounts.models import RFID


def read_rfid(mfrc=None, cleanup=True) -> dict:
    """Read a single RFID tag using the MFRC522 reader."""
    try:
        if mfrc is None:
            from mfrc522 import MFRC522  # type: ignore
            mfrc = MFRC522()
    except Exception as exc:  # pragma: no cover - hardware dependent
        return {"error": str(exc)}

    try:
        import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
    except Exception:  # pragma: no cover - hardware dependent
        GPIO = None

    try:
        timeout = time.time() + 1
        while time.time() < timeout:  # pragma: no cover - hardware loop
            (status, _tag_type) = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
            if status == mfrc.MI_OK:
                (status, uid) = mfrc.MFRC522_Anticoll()
                if status == mfrc.MI_OK:
                    rfid = "".join(f"{x:02X}" for x in uid[:5])
                    tag, created = RFID.objects.get_or_create(rfid=rfid)
                    result = {
                        "rfid": rfid,
                        "label_id": tag.pk,
                        "created": created,
                        "color": tag.color,
                        "allowed": tag.allowed,
                        "released": tag.released,
                    }
                    try:
                        from nodes.notifications import notify

                        status_text = "Ok" if tag.allowed else "Not Ok"
                        line1 = f"Label {tag.label_id} {status_text}"
                        notify(line1, rfid)
                    except Exception:
                        pass
                    return result
            time.sleep(0.2)
        return {"rfid": None, "label_id": None}
    except Exception as exc:  # pragma: no cover - hardware dependent
        return {"error": str(exc)}
    finally:  # pragma: no cover - cleanup hardware
        if cleanup and GPIO:
            try:
                GPIO.cleanup()
            except Exception:
                pass
