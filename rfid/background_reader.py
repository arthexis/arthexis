"""Background RFID reader using IRQ pin events."""
import atexit
import logging
import os
import queue
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover - hardware dependent
    import RPi.GPIO as GPIO  # type: ignore
except Exception:  # pragma: no cover - hardware dependent
    GPIO = None  # type: ignore

IRQ_PIN = int(os.environ.get("RFID_IRQ_PIN", "4"))
_tag_queue: "queue.Queue[dict]" = queue.Queue()
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_reader = None
_current: Optional[dict] = None


def _irq_callback(channel):  # pragma: no cover - hardware dependent
    logger.debug("IRQ callback triggered on channel %s", channel)
    from .reader import read_rfid

    global _current
    result = read_rfid(mfrc=_reader, cleanup=False)
    if result.get("error"):
        logger.warning("RFID read error via IRQ: %s", result["error"])
    elif result.get("rfid"):
        logger.info("RFID tag detected via IRQ: %s", result.get("rfid"))
        try:
            _reader.dev_write(_reader.ComIrqReg, 0x7F)
        except Exception:  # pragma: no cover - hardware dependent
            pass
        result["start"] = time.time()
        result["duration"] = 0
        result["last_notified"] = 0
        _current = result
        try:
            from nodes.notifications import notify

            status_text = "OK" if result.get("allowed") else "Not OK"
            color_char = (result.get("color") or "")[:1].upper()
            line1 = f"RFID {result['label_id']} {status_text} {color_char}".strip()
            line2 = f"{result['rfid']} 0s"
            notify(line1, line2)
        except Exception:  # pragma: no cover - notifications best effort
            pass
    _tag_queue.put(result)


def _setup_hardware():  # pragma: no cover - hardware dependent
    global _reader
    if GPIO is None:
        logger.warning("GPIO library not available; RFID reader disabled")
        return False
    try:
        from mfrc522 import MFRC522  # type: ignore
    except Exception as exc:
        logger.warning("MFRC522 library not available: %s", exc)
        return False

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(IRQ_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.debug("Initialized GPIO on IRQ pin %s", IRQ_PIN)

        _reader = MFRC522()
        try:
            # Enable interrupts for card detection (best effort)
            _reader.dev_write(_reader.ComIEnReg, 0xA0)  # enable IdleIrq
            _reader.dev_write(_reader.ComIrqReg, 0x7F)
        except Exception:
            pass
        GPIO.add_event_detect(IRQ_PIN, GPIO.FALLING, callback=_irq_callback)
        logger.info("RFID IRQ listener active on pin %s", IRQ_PIN)
    except Exception as exc:
        logger.warning("Failed to initialize RFID hardware: %s", exc)
        try:
            GPIO.cleanup()
        except Exception:
            pass
        return False
    return True


def _worker():  # pragma: no cover - background thread
    if not _setup_hardware():
        logger.error("RFID hardware setup failed; background reader not running")
        return
    while not _stop_event.is_set():
        _stop_event.wait(0.5)
    if GPIO:
        try:
            GPIO.remove_event_detect(IRQ_PIN)
            GPIO.cleanup()
        except Exception:
            pass


def start():
    """Start the background RFID reader."""
    global _thread
    if GPIO is None:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    logger.debug("Starting RFID background reader thread")
    _thread = threading.Thread(target=_worker, name="rfid-reader", daemon=True)
    _thread.start()
    atexit.register(stop)


def stop():
    """Stop the background RFID reader and cleanup GPIO."""
    global _current
    _stop_event.set()
    if _thread:
        _thread.join(timeout=1)
    _current = None
    if GPIO:
        try:
            if GPIO.getmode() is not None:  # Only cleanup if GPIO was initialized
                GPIO.cleanup()
        except Exception:
            pass


def get_next_tag(timeout: float = 0) -> Optional[dict]:
    """Retrieve the next tag read from the queue.

    Falls back to direct polling if no IRQ events are queued.
    """
    global _current
    try:
        tag = _tag_queue.get(timeout=timeout)
        if tag and tag.get("rfid"):
            _current = tag
        return tag
    except queue.Empty:
        logger.debug("IRQ queue empty; falling back to direct read")
        try:
            from .reader import read_rfid

            if _current:
                res = read_rfid(mfrc=_reader, cleanup=False, timeout=0.1)
                if res.get("rfid") == _current.get("rfid"):
                    duration = int(time.time() - _current["start"])
                    if duration > _current.get("last_notified", 0):
                        try:
                            from nodes.notifications import notify

                            status_text = "OK" if _current.get("allowed") else "Not OK"
                            color_char = (_current.get("color") or "")[:1].upper()
                            line1 = f"RFID {_current['label_id']} {status_text} {color_char}".strip()
                            line2 = f"{_current['rfid']} {duration}s"
                            notify(line1, line2)
                        except Exception:  # pragma: no cover
                            pass
                        _current["last_notified"] = duration
                    _current["duration"] = duration
                    return _current
                else:
                    duration = int(time.time() - _current["start"])
                    if duration >= 0 and duration > _current.get("last_notified", 0):
                        try:
                            from nodes.notifications import notify

                            status_text = "OK" if _current.get("allowed") else "Not OK"
                            color_char = (_current.get("color") or "")[:1].upper()
                            line1 = f"RFID {_current['label_id']} {status_text} {color_char}".strip()
                            line2 = f"{_current['rfid']} {duration}s"
                            notify(line1, line2)
                        except Exception:  # pragma: no cover
                            pass
                    _current["duration"] = duration
                    result = _current
                    _current = None
                    return result
            else:
                res = read_rfid(mfrc=_reader, cleanup=False)
                if res.get("rfid") or res.get("error"):
                    logger.debug("Polling read result: %s", res)
                    return res
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.debug("Polling read failed: %s", exc)
        return None
