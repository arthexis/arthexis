"""Background RFID reader using IRQ pin events."""
import atexit
import logging
import os
import queue
import threading
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


def _irq_callback(channel):  # pragma: no cover - hardware dependent
    from .reader import read_rfid

    result = read_rfid(mfrc=_reader, cleanup=False)
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
        GPIO.setup(IRQ_PIN, GPIO.IN)

        _reader = MFRC522()
        try:
            # Enable interrupts for card detection (best effort)
            _reader.dev_write(_reader.ComIEnReg, 0xA0)  # enable IdleIrq
            _reader.dev_write(_reader.ComIrqReg, 0x7F)
        except Exception:
            pass

        GPIO.add_event_detect(IRQ_PIN, GPIO.FALLING, callback=_irq_callback)
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
    _thread = threading.Thread(target=_worker, name="rfid-reader", daemon=True)
    _thread.start()
    atexit.register(stop)


def stop():
    """Stop the background RFID reader and cleanup GPIO."""
    _stop_event.set()
    if _thread:
        _thread.join(timeout=1)
    if GPIO:
        try:
            if GPIO.getmode() is not None:  # Only cleanup if GPIO was initialized
                GPIO.cleanup()
        except Exception:
            pass


def get_next_tag(timeout: float = 0) -> Optional[dict]:
    """Retrieve the next tag read from the queue."""
    try:
        return _tag_queue.get(timeout=timeout)
    except queue.Empty:
        return None
