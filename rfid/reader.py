import time
from django.utils import timezone
from accounts.models import RFID


def _hex_to_bytes(value: str) -> list[int]:
    return [int(value[i : i + 2], 16) for i in range(0, len(value), 2)]


def read_rfid(
    mfrc=None,
    cleanup: bool = True,
    timeout: float = 1.0,
    poll_interval: float | None = 0.05,
    use_irq: bool = False,
) -> dict:
    """Read a single RFID tag using the MFRC522 reader.

    Args:
        mfrc: Optional MFRC522 reader instance.
        cleanup: Whether to call ``GPIO.cleanup`` on exit.
        timeout: How long to poll for a card before giving up.
        poll_interval: Delay between polling attempts.  Set to ``None`` or
            ``0`` to skip sleeping (useful when hardware interrupts are
            configured).
        use_irq: If ``True``, do not sleep between polls regardless of
            ``poll_interval``.
    """
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
                    tag.last_seen_on = timezone.now()
                    mfrc.MFRC522_SelectTag(uid)
                    try:
                        from msg import notify

                        notify(f"RFID {rfid}", "Hold on reader")
                    except Exception:
                        pass
                    default_key = [0xFF] * 6
                    sector_data: list[list[str | None]] = []
                    key_a_ok = False
                    key_b_ok = False
                    for sector in range(16):
                        sector_blocks: list[str | None] = []
                        for block_offset in range(4):
                            block = sector * 4 + block_offset
                            data_hex: str | None = None
                            # Try Key A first
                            key_a_bytes = _hex_to_bytes(tag.key_a or "FFFFFFFFFFFF")
                            if (
                                mfrc.MFRC522_Auth(
                                    mfrc.PICC_AUTHENT1A, block, key_a_bytes, uid
                                )
                                == mfrc.MI_OK
                            ):
                                key_a_ok = True
                                data = mfrc.MFRC522_Read(block)
                                if data:
                                    data_hex = "".join(f"{x:02X}" for x in data)
                            elif (
                                mfrc.MFRC522_Auth(
                                    mfrc.PICC_AUTHENT1A, block, default_key, uid
                                )
                                == mfrc.MI_OK
                            ):
                                key_a_ok = True
                                tag.key_a = "FFFFFFFFFFFF"
                                data = mfrc.MFRC522_Read(block)
                                if data:
                                    data_hex = "".join(f"{x:02X}" for x in data)
                            else:
                                # Try Key B if Key A failed
                                key_b_bytes = _hex_to_bytes(tag.key_b or "FFFFFFFFFFFF")
                                if (
                                    mfrc.MFRC522_Auth(
                                        mfrc.PICC_AUTHENT1B, block, key_b_bytes, uid
                                    )
                                    == mfrc.MI_OK
                                ):
                                    key_b_ok = True
                                    data = mfrc.MFRC522_Read(block)
                                    if data:
                                        data_hex = "".join(f"{x:02X}" for x in data)
                                elif (
                                    mfrc.MFRC522_Auth(
                                        mfrc.PICC_AUTHENT1B, block, default_key, uid
                                    )
                                    == mfrc.MI_OK
                                ):
                                    key_b_ok = True
                                    tag.key_b = "FFFFFFFFFFFF"
                                    data = mfrc.MFRC522_Read(block)
                                    if data:
                                        data_hex = "".join(f"{x:02X}" for x in data)
                            sector_blocks.append(data_hex)
                        sector_data.append(sector_blocks)
                    mfrc.MFRC522_StopCrypto1()
                    tag.data = sector_data
                    tag.key_a_verified = key_a_ok
                    tag.key_b_verified = key_b_ok
                    tag.save(
                        update_fields=[
                            "last_seen_on",
                            "data",
                            "key_a_verified",
                            "key_b_verified",
                            "key_a",
                            "key_b",
                        ]
                    )
                    result = {
                        "rfid": rfid,
                        "label_id": tag.pk,
                        "created": created,
                        "color": tag.color,
                        "allowed": tag.allowed,
                        "released": tag.released,
                        "reference": tag.reference.value if tag.reference else None,
                        "data": tag.data,
                        "key_a_verified": tag.key_a_verified,
                        "key_b_verified": tag.key_b_verified,
                    }
                    try:
                        from msg import notify

                        status_text = "OK" if tag.allowed else "BAD"
                        color_word = (tag.color or "").upper()
                        # Display scan results on the LCD in the format:
                        #   Row 1: "RFID <label> <OK/BAD>"
                        #   Row 2: "<rfid> <COLOR>"
                        subject = f"RFID {tag.label_id} {status_text}".strip()
                        body = f"{rfid} {color_word}".strip()
                        notify(subject, body)
                    except Exception:
                        pass
                    return result
            if not use_irq and poll_interval:
                time.sleep(poll_interval)
        return {"rfid": None, "label_id": None}
    except Exception as exc:  # pragma: no cover - hardware dependent
        try:
            from msg import notify

            if 'rfid' in locals():
                notify(f"RFID {rfid}", "Read failed")
        except Exception:
            pass
        return {"error": str(exc)}
    finally:  # pragma: no cover - cleanup hardware
        if cleanup and GPIO:
            try:
                GPIO.cleanup()
            except Exception:
                pass
