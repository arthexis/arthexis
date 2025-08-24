import time
from accounts.models import RFID


def read_rfid(mfrc=None, cleanup=True, timeout: float = 1.0) -> dict:
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
        end = time.time() + timeout
        while time.time() < end:  # pragma: no cover - hardware loop
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
                        "reference": tag.reference.value if tag.reference else None,
                    }
                    try:
                        from nodes.notifications import notify

                        status_text = "OK" if tag.allowed else "BAD"
                        privacy = "PUB" if tag.released else "INT"
                        color_word = (tag.color or "").upper()
                        subject = f"RFID {tag.label_id} {status_text} {privacy}".strip()
                        body = f"{rfid} {color_word}".strip()
                        notify(subject, body)
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
