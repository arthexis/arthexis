"""Desktop and log notifications for local Arthexis operators."""

from __future__ import annotations

import logging
import sys
import threading

try:  # pragma: no cover - optional dependency
    from plyer import notification as plyer_notification
except Exception:  # pragma: no cover - plyer may not be installed
    plyer_notification = None

logger = logging.getLogger(__name__)


def supports_gui_toast() -> bool:
    """Return whether a local Windows toast notification is available."""

    return sys.platform.startswith("win") and callable(
        getattr(plyer_notification, "notify", None)
    )


class NotificationManager:
    """Deliver a notification to a desktop toast or the application log."""

    def send(self, subject: str, body: str = "") -> bool:
        """Deliver a best-effort operator notification."""

        if supports_gui_toast():
            try:  # pragma: no cover - platform dependent
                plyer_notification.notify(
                    title="Arthexis", message=f"{subject}\n{body}", timeout=6
                )
                return True
            except Exception:  # pragma: no cover - platform dependent
                logger.debug("Windows notification failed", exc_info=True)
        logger.info("%s %s", subject, body)
        return True

    def send_async(self, subject: str, body: str = "") -> None:
        """Deliver :meth:`send` without delaying the caller."""

        threading.Thread(target=self.send, args=(subject, body), daemon=True).start()


manager = NotificationManager()


def notify(subject: str, body: str = "") -> bool:
    """Deliver a local operator notification."""

    return manager.send(subject, body)


def notify_async(subject: str, body: str = "") -> None:
    """Deliver a local operator notification asynchronously."""

    manager.send_async(subject, body)
