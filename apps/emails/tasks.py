from __future__ import annotations

from celery import shared_task


def _poll_emails() -> None:
    """Poll all configured email collectors for new messages."""
    try:
        from apps.emails.models import EmailCollector
    except Exception:  # pragma: no cover - app not ready
        return

    for collector in EmailCollector.objects.filter(is_enabled=True):
        collector.collect()


poll_emails = shared_task(_poll_emails)


__all__ = ["_poll_emails", "poll_emails"]
