import logging
import threading
from typing import Optional

from .background_reader import get_next_tag
from .signals import tag_scanned

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _worker() -> None:  # pragma: no cover - background thread
    logger.debug("RFID watch thread started")
    while not _stop.is_set():
        # Use a shorter timeout for faster responsiveness when polling for
        # new tags from the background reader.
        result = get_next_tag(timeout=0.1)
        if result and result.get("rfid"):
            logger.info("RFID tag detected: %s", result.get("rfid"))
            tag_scanned.send(sender=None, **result)
    logger.debug("RFID watch thread exiting")


def start() -> None:
    """Start the always-on RFID watcher."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_worker, name="rfid-watch", daemon=True)
    _thread.start()


def stop() -> None:
    """Stop the always-on RFID watcher."""
    _stop.set()
    if _thread:
        _thread.join(timeout=1)


def is_running() -> bool:
    """Return ``True`` if the watcher thread is active."""
    return bool(_thread and _thread.is_alive())
