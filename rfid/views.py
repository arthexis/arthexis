from django.http import JsonResponse
from django.shortcuts import render
from website.utils import landing
from django.urls import reverse

from accounts.models import RFID


def read_rfid() -> dict:
    """Read a single RFID tag using the MFRC522 reader."""
    try:
        from mfrc522 import MFRC522  # type: ignore
    except Exception as exc:  # pragma: no cover - hardware dependent
        return {"error": str(exc)}

    import time
    try:
        import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
    except Exception:  # pragma: no cover - hardware dependent
        GPIO = None

    try:
        mfrc = MFRC522()
        timeout = time.time() + 1
        while time.time() < timeout:  # pragma: no cover - hardware loop
            (status, _tag_type) = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
            if status == mfrc.MI_OK:
                (status, uid) = mfrc.MFRC522_Anticoll()
                if status == mfrc.MI_OK:
                    rfid = "".join(f"{x:02X}" for x in uid[:5])
                    tag, created = RFID.objects.get_or_create(rfid=rfid)
                    mfrc.MFRC522_SelectTag(uid)
                    block = 8
                    payload = None
                    for key_hex, mode in (
                        (tag.key_a, mfrc.PICC_AUTHENT1A),
                        (tag.key_b, mfrc.PICC_AUTHENT1B),
                    ):
                        key = [int(key_hex[i : i + 2], 16) for i in range(0, 12, 2)]
                        status = mfrc.MFRC522_Auth(mode, block, key, uid)
                        if status == mfrc.MI_OK:
                            data = mfrc.MFRC522_Read(block)
                            raw = bytes(data)
                            tag.block_data = raw
                            tag.save(update_fields=["block_data"])
                            decoded = raw.rstrip(b"\x00").decode(
                                "ascii", errors="ignore"
                            )
                            if not decoded:
                                decoded = raw.hex()
                            payload = decoded
                            break
                    mfrc.MFRC522_StopCrypto1()
                    return {
                        "rfid": rfid,
                        "label_id": tag.pk,
                        "created": created,
                        "color": tag.color,
                        "allowed": tag.allowed,
                        "released": tag.released,
                        "block_data": payload,
                    }
            time.sleep(0.2)
        return {"rfid": None, "label_id": None}
    except Exception as exc:  # pragma: no cover - hardware dependent
        return {"error": str(exc)}
    finally:  # pragma: no cover - cleanup hardware
        if GPIO:
            try:
                GPIO.cleanup()
            except Exception:
                pass


def scan_next(_request):
    """Return the next scanned RFID tag."""
    result = read_rfid()
    if result.get("error"):
        return JsonResponse({"error": result["error"]}, status=500)
    return JsonResponse(result)


@landing("RFID Reader")
def reader(request):
    """Public page to read RFID tags."""
    context = {"scan_url": reverse("rfid-scan-next")}
    return render(request, "rfid/reader.html", context)
