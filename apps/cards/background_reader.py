"""Background RFID reader using IRQ pin events."""

import atexit
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from django.conf import settings

from .constants import DEFAULT_IRQ_PIN, DEFAULT_RST_PIN, GPIO_PIN_MODE_BCM
from .reader import resolve_spi_bus_device, resolve_spi_device_path

logger = logging.getLogger(__name__)

try:  # pragma: no cover - hardware dependent
    import RPi.GPIO as GPIO  # type: ignore
except Exception:  # pragma: no cover - hardware dependent
    GPIO = None  # type: ignore

IRQ_PIN = int(os.environ.get("RFID_IRQ_PIN", str(DEFAULT_IRQ_PIN)))
_tag_queue: "queue.Queue[dict]" = queue.Queue()
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_reader = None
_auto_detect_logged = False
_last_setup_failure: float | None = None
_last_auto_detect_failure: float | None = None
_last_not_configured_log = 0.0
_auto_detect_lock = threading.Lock()
_log_throttle_lock = threading.Lock()
_irq_empty_lock = threading.Lock()
_suite_marker = f"{os.getpid()}:{int(time.time())}"

try:  # pragma: no cover - debugging helper not available on all platforms
    import resource
except Exception:  # pragma: no cover - defensive fallback
    resource = None

_FD_SNAPSHOT_THRESHOLD = int(os.environ.get("RFID_FD_LOG_THRESHOLD", "400"))
_FD_SNAPSHOT_INTERVAL = float(os.environ.get("RFID_FD_LOG_INTERVAL", "10"))
_last_fd_snapshot = 0.0
_SETUP_BACKOFF_SECONDS = float(os.environ.get("RFID_SETUP_BACKOFF_SECONDS", "30"))
_AUTO_DETECT_BACKOFF_SECONDS = float(
    os.environ.get("RFID_AUTO_DETECT_BACKOFF_SECONDS", "30")
)
_NOT_CONFIGURED_LOG_INTERVAL = float(
    os.environ.get("RFID_NOT_CONFIGURED_LOG_INTERVAL", "30")
)
_irq_empty_count = 0
_irq_empty_since = 0.0


def _record_irq_empty() -> None:
    """Track consecutive empty IRQ queue polls."""
    global _irq_empty_count, _irq_empty_since
    now = time.monotonic()
    with _irq_empty_lock:
        if _irq_empty_count == 0:
            _irq_empty_since = now
        _irq_empty_count += 1


def _log_irq_empty_summary(event: str) -> None:
    """Log a summary of consecutive empty IRQ polls before a successful read."""
    global _irq_empty_count, _irq_empty_since
    with _irq_empty_lock:
        if _irq_empty_count == 0:
            return
        count = _irq_empty_count
        start = _irq_empty_since
        _irq_empty_count = 0
        _irq_empty_since = 0.0
    waited = max(0.0, time.monotonic() - start)
    logger.debug(
        "IRQ queue empty; fell back to direct read %s times over %.2fs before %s",
        count,
        waited,
        event,
    )


def _log_fd_snapshot(label: str) -> None:
    """Emit a lightweight file descriptor snapshot for debugging leaks."""

    global _last_fd_snapshot

    now = time.monotonic()
    if _FD_SNAPSHOT_INTERVAL > 0 and now - _last_fd_snapshot < _FD_SNAPSHOT_INTERVAL:
        return

    fd_dir = Path("/proc/self/fd")
    if not fd_dir.exists():  # pragma: no cover - platform check
        return

    try:
        entries = list(fd_dir.iterdir())
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.debug("RFID fd snapshot unavailable (%s): %s", label, exc)
        return

    count = len(entries)
    if _FD_SNAPSHOT_INTERVAL > 0:
        _last_fd_snapshot = now

    limits = ()
    if resource:
        try:
            limits = resource.getrlimit(resource.RLIMIT_NOFILE)
        except Exception:  # pragma: no cover - defensive guard
            limits = ()

    samples: list[str] = []
    for fd_path in entries[:10]:
        try:
            samples.append(str(fd_path.resolve()))
        except Exception as exc:  # pragma: no cover - defensive guard
            samples.append(f"{fd_path.name}:<error:{exc}>")

    message = (
        "RFID fd snapshot (%s): count=%s limits=%s sample=%s"
        % (label, count, limits, samples)
    )
    if count >= _FD_SNAPSHOT_THRESHOLD:
        logger.warning(message)
    else:
        logger.debug(message)


def _record_setup_failure(reason: str) -> None:
    """Track setup failures to avoid thrashing hardware when unavailable."""

    global _last_setup_failure
    _last_setup_failure = time.monotonic()
    logger.warning(
        "RFID hardware setup failed (%s); skipping retries for %.1fs",
        reason,
        _SETUP_BACKOFF_SECONDS,
    )


def _lock_path() -> Path:
    """Return the sentinel file that marks an installed RFID reader."""

    return Path(settings.BASE_DIR) / ".locks" / "rfid.lck"


def lock_file_path() -> Path:
    """Public accessor for the RFID lock file path."""

    return _lock_path()


def _read_lock_marker(lock: Path) -> str | None:
    """Return the stored suite marker for a lock file when available."""

    try:
        contents = lock.read_text(encoding="utf-8").strip()
        return contents or None
    except Exception:
        return None


def _lock_matches_current(lock: Path) -> bool:
    """Return ``True`` when the lock file belongs to this suite instance."""

    marker = _read_lock_marker(lock)
    return bool(marker and marker == _suite_marker)


def lock_file_active() -> tuple[bool, Path]:
    """Return whether a current-suite lock file exists and its path."""

    lock = lock_file_path()
    if lock.exists():
        if _lock_matches_current(lock):
            return True, lock
        try:
            lock.unlink()
            logger.info("Removed stale RFID lock file from previous suite: %s", lock)
        except Exception as exc:  # pragma: no cover - defensive filesystem guard
            logger.debug("Unable to remove stale RFID lock file %s: %s", lock, exc)
    return False, lock


def _mark_scanner_used() -> None:
    """Update the RFID lock file timestamp to record scanner usage."""

    lock = _lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(_suite_marker, encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive filesystem fallback
        logger.debug("RFID auto-detect: unable to update lock file %s: %s", lock, exc)


def _spi_device_path() -> Path:
    """Return the expected SPI device path for the RFID reader."""
    return resolve_spi_device_path()


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
    global _auto_detect_logged, _last_auto_detect_failure

    has_lock, lock = lock_file_active()
    if has_lock:
        return True

    env_flag = os.environ.get("RFID_AUTO_DETECT", "").lower()
    if env_flag and env_flag not in {"1", "true", "yes"}:
        return False

    with _auto_detect_lock:
        now = time.monotonic()
        if (
            _last_auto_detect_failure is not None
            and _AUTO_DETECT_BACKOFF_SECONDS > 0
            and now - _last_auto_detect_failure < _AUTO_DETECT_BACKOFF_SECONDS
        ):
            return False

        detected = _auto_detect_configured()
        if detected and not _auto_detect_logged:
            logger.info(
                "RFID reader detected without lock file using SPI device %s",
                _spi_device_path(),
            )
            _auto_detect_logged = True
            _last_auto_detect_failure = None
        elif not detected:
            _last_auto_detect_failure = now
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

        spi_bus, spi_device = resolve_spi_bus_device()
        _reader = MFRC522(
            bus=spi_bus,
            device=spi_device,
            pin_mode=GPIO_PIN_MODE_BCM,
            pin_rst=DEFAULT_RST_PIN,
        )
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
    global _thread, _last_setup_failure

    _log_fd_snapshot("worker-start")
    if not _setup_hardware():
        _record_setup_failure("initialization")
        _log_fd_snapshot("worker-setup-failed")
        lock = lock_file_path()
        if lock.exists():
            try:
                lock.unlink()
                logger.info("Removed stale RFID lock file after setup failure: %s", lock)
            except Exception as exc:
                logger.debug(
                    "Unable to remove RFID lock file %s after setup failure: %s", lock, exc
                )
        _thread = None
        return
    _last_setup_failure = None
    _log_fd_snapshot("worker-setup-ok")
    # Wait indefinitely until a stop is requested, relying solely on IRQ
    # callbacks to populate the tag queue. This avoids periodic polling and
    # lets the thread sleep until explicitly stopped.
    _stop_event.wait()
    _log_fd_snapshot("worker-stop-event")
    if GPIO:
        try:
            GPIO.remove_event_detect(IRQ_PIN)
            GPIO.cleanup()
        except Exception:
            pass
    _thread = None


def start():
    """Start the background RFID reader."""
    global _thread
    now = time.monotonic()
    if (
        _last_setup_failure is not None
        and _SETUP_BACKOFF_SECONDS > 0
        and now - _last_setup_failure < _SETUP_BACKOFF_SECONDS
    ):
        remaining = _SETUP_BACKOFF_SECONDS - (now - _last_setup_failure)
        logger.info(
            "RFID background reader start skipped; last setup failure %.1fs ago "
            "(retry in %.1fs)",
            now - _last_setup_failure,
            remaining,
        )
        return

    if not is_configured():
        logger.debug("RFID not configured; background reader not started")
        return
    if GPIO is None:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    logger.debug("Starting RFID background reader thread")
    _log_fd_snapshot("start")
    _thread = threading.Thread(target=_worker, name="rfid-reader", daemon=True)
    _thread.start()
    _mark_scanner_used()
    atexit.register(stop)


def stop():
    """Stop the background RFID reader and cleanup GPIO."""
    global _thread

    _stop_event.set()
    if _thread:
        _thread.join(timeout=1)
    _log_fd_snapshot("stop")
    if GPIO:
        try:
            if GPIO.getmode() is not None:  # Only cleanup if GPIO was initialized
                GPIO.cleanup()
        except Exception:
            pass
    _thread = None


def get_next_tag(timeout: float | None = 0) -> Optional[dict]:
    """Retrieve the next tag read from the queue.

    Falls back to direct polling if no IRQ events are queued.
    """
    global _last_not_configured_log
    if not is_configured():
        with _log_throttle_lock:
            now = time.monotonic()
            if (
                _NOT_CONFIGURED_LOG_INTERVAL <= 0
                or now - _last_not_configured_log >= _NOT_CONFIGURED_LOG_INTERVAL
            ):
                logger.debug("RFID not configured; skipping read")
                _last_not_configured_log = now
        return None
    if timeout is None:
        timeout = 0.0
    timeout = max(0.0, timeout)
    start_time = time.monotonic()
    try:
        result = _tag_queue.get(timeout=timeout)
        if result and result.get("rfid"):
            _mark_scanner_used()
        _log_irq_empty_summary("queue read")
        return result
    except queue.Empty:
        _log_fd_snapshot("get_next_tag-empty")
        _record_irq_empty()
        try:
            from .reader import read_rfid

            elapsed = time.monotonic() - start_time
            remaining_timeout = max(0.0, timeout - elapsed)
            res = read_rfid(mfrc=_reader, cleanup=False, timeout=remaining_timeout)
            if res.get("rfid") or res.get("error"):
                _log_irq_empty_summary("polling read")
                logger.debug("Polling read result: %s", res)
                if res.get("rfid"):
                    _mark_scanner_used()
                return res
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.debug("Polling read failed: %s", exc)
        return None
