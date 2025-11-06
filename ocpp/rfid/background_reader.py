"""Background RFID reader using IRQ pin events."""

import atexit
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Optional

from django.conf import settings

from .constants import DEFAULT_IRQ_PIN, SPI_BUS, SPI_DEVICE

logger = logging.getLogger(__name__)

try:  # pragma: no cover - hardware dependent
    import RPi.GPIO as GPIO  # type: ignore
except Exception:  # pragma: no cover - hardware dependent
    GPIO = None  # type: ignore

IRQ_PIN = int(os.environ.get("RFID_IRQ_PIN", str(DEFAULT_IRQ_PIN)))
_DEFAULT_SPI_DEVICE = Path(f"/dev/spidev{SPI_BUS}.{SPI_DEVICE}")
_tag_queue: "queue.Queue[dict]" = queue.Queue()
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_reader = None
_auto_detect_logged = False


def _lock_path() -> Path:
    """Return the sentinel file that marks an installed RFID reader."""

    return Path(settings.BASE_DIR) / "locks" / "rfid.lck"


def lock_file_path() -> Path:
    """Public accessor for the RFID lock file path."""

    return _lock_path()


def _mark_scanner_used() -> None:
    """Update the RFID lock file timestamp to record scanner usage."""

    lock = _lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
    except Exception as exc:  # pragma: no cover - defensive filesystem fallback
        logger.debug("RFID auto-detect: unable to update lock file %s: %s", lock, exc)


def _spi_device_path() -> Path:
    """Return the expected SPI device path for the RFID reader."""

    override = os.environ.get("RFID_SPI_DEVICE")
    if override:
        return Path(override)
    return _DEFAULT_SPI_DEVICE


def _available_spi_devices() -> list[Path]:
    """Return the list of detected SPI devices on the system."""

    try:  # pragma: no cover - filesystem availability varies
        return sorted(Path("/dev").glob("spidev*"))
    except Exception:  # pragma: no cover - defensive fallback
        return []


def _ensure_gpio_loaded() -> bool:
    """Ensure the GPIO library is importable for hardware access."""

    global GPIO
    if GPIO is not None:
        return True
    try:  # pragma: no cover - hardware dependent import
        import RPi.GPIO as gpio_mod  # type: ignore
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.debug("RFID auto-detect: RPi.GPIO unavailable: %s", exc)
        return False
    GPIO = gpio_mod
    return True


def _dependencies_available() -> bool:
    """Return ``True`` when hardware libraries required for RFID are present."""

    if not _ensure_gpio_loaded():
        return False
    try:  # pragma: no cover - hardware dependent import
        from mfrc522 import MFRC522  # type: ignore
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.debug("RFID auto-detect: MFRC522 unavailable: %s", exc)
        return False
    return True


def _has_spi_device() -> bool:
    """Return ``True`` if the configured SPI device exists on this host."""

    device = _spi_device_path()
    if device.exists():  # pragma: no cover - filesystem probe
        return True
    candidates = _available_spi_devices()
    if candidates:
        logger.debug(
            "RFID auto-detect: expected SPI device %s not found; available devices: %s",
            device,
            ", ".join(str(candidate) for candidate in candidates),
        )
    else:
        logger.debug(
            "RFID auto-detect: expected SPI device %s not found and no SPI devices detected",
            device,
        )
    return False


def _auto_detect_configured() -> bool:
    """Best-effort detection of a connected RFID reader without lock files."""

    if not _has_spi_device():
        return False
    if not _dependencies_available():
        return False
    return True


def is_configured() -> bool:
    """Return ``True`` if an RFID reader is configured for this node."""
    global _auto_detect_logged

    lock = lock_file_path()
    if lock.exists():
        return True

    detected = _auto_detect_configured()
    if detected and not _auto_detect_logged:
        logger.info(
            "RFID reader detected without lock file using SPI device %s",
            _spi_device_path(),
        )
        _auto_detect_logged = True
    return detected


def _irq_callback(channel):  # pragma: no cover - hardware dependent
    logger.debug("IRQ callback triggered on channel %s", channel)
    from .reader import read_rfid

    result = read_rfid(mfrc=_reader, cleanup=False, use_irq=True)
    if result.get("error"):
        logger.warning("RFID read error via IRQ: %s", result["error"])
    elif result.get("rfid"):
        logger.info("RFID tag detected via IRQ: %s", result.get("rfid"))
        try:
            _reader.dev_write(_reader.ComIrqReg, 0x7F)
        except Exception:  # pragma: no cover - hardware dependent
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
    # Wait indefinitely until a stop is requested, relying solely on IRQ
    # callbacks to populate the tag queue. This avoids periodic polling and
    # lets the thread sleep until explicitly stopped.
    _stop_event.wait()
    if GPIO:
        try:
            GPIO.remove_event_detect(IRQ_PIN)
            GPIO.cleanup()
        except Exception:
            pass


def start():
    """Start the background RFID reader."""
    global _thread
    if not is_configured():
        logger.debug("RFID not configured; background reader not started")
        return
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
    """Retrieve the next tag read from the queue.

    Falls back to direct polling if no IRQ events are queued.
    """
    if not is_configured():
        logger.debug("RFID not configured; skipping read")
        return None
    try:
        result = _tag_queue.get(timeout=timeout)
        if result and result.get("rfid"):
            _mark_scanner_used()
        return result
    except queue.Empty:
        logger.debug("IRQ queue empty; falling back to direct read")
        try:
            from .reader import read_rfid

            res = read_rfid(mfrc=_reader, cleanup=False)
            if res.get("rfid") or res.get("error"):
                logger.debug("Polling read result: %s", res)
                if res.get("rfid"):
                    _mark_scanner_used()
                return res
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.debug("Polling read failed: %s", exc)
        return None
