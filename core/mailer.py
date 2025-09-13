import logging
from typing import Sequence

from django.conf import settings
from post_office import mail
from post_office.connections import connections as po_connections

logger = logging.getLogger(__name__)


def send(
    subject: str,
    message: str,
    recipient_list: Sequence[str],
    from_email: str | None = None,
    *,
    outbox=None,
    **kwargs,
):
    """Queue an email using post_office, optionally via an EmailOutbox.

    If ``outbox`` is provided, its connection is registered under a unique
    backend alias so that Post Office can use it when dispatching.
    """
    sender = (
        from_email
        or getattr(outbox, "from_email", None)
        or settings.DEFAULT_FROM_EMAIL
    )
    backend = ""
    if outbox is not None:
        alias = f"outbox_{getattr(outbox, 'pk', 'tmp')}"
        if not hasattr(settings, "POST_OFFICE"):
            settings.POST_OFFICE = {}
        settings.POST_OFFICE.setdefault("BACKENDS", {})[alias] = (
            "django.core.mail.backends.smtp.EmailBackend"
        )
        if not hasattr(po_connections._connections, "connections"):
            po_connections._connections.connections = {}
        po_connections._connections.connections[alias] = outbox.get_connection()
        backend = alias
        logger.info("Queueing email via EmailOutbox %s", alias)
    kwargs.pop("fail_silently", None)
    return mail.send(
        recipients=recipient_list,
        sender=sender,
        subject=subject,
        message=message,
        backend=backend,
        **kwargs,
    )
