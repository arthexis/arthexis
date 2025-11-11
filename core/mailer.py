import logging
from typing import Sequence

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils.module_loading import import_string

try:  # pragma: no cover - import should always succeed but guard defensively
    from django.core.mail.backends.dummy import (
        EmailBackend as DummyEmailBackend,
    )
except Exception:  # pragma: no cover - fallback when dummy backend unavailable
    DummyEmailBackend = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def send(
    subject: str,
    message: str,
    recipient_list: Sequence[str],
    from_email: str | None = None,
    *,
    outbox=None,
    attachments: Sequence[tuple[str, str, str]] | None = None,
    content_subtype: str | None = None,
    **kwargs,
):
    """Send an email using Django's email utilities.

    If ``outbox`` is provided, its connection will be used when sending.
    """
    sender = (
        from_email or getattr(outbox, "from_email", None) or settings.DEFAULT_FROM_EMAIL
    )
    connection = outbox.get_connection() if outbox is not None else None
    fail_silently = kwargs.pop("fail_silently", False)
    email = EmailMessage(
        subject=subject,
        body=message,
        from_email=sender,
        to=list(recipient_list),
        connection=connection,
        **kwargs,
    )
    if attachments:
        for attachment in attachments:
            if isinstance(attachment, (list, tuple)):
                length = len(attachment)
                if length not in {2, 3}:
                    raise ValueError(
                        "attachments must contain 2- or 3-item (name, content, mimetype) tuples"
                    )
                email.attach(*attachment)
            else:
                email.attach(attachment)
    if content_subtype:
        email.content_subtype = content_subtype
    email.send(fail_silently=fail_silently)
    return email


def can_send_email() -> bool:
    """Return ``True`` when at least one outbound email path is configured."""

    from teams.models import EmailOutbox  # imported lazily to avoid circular deps

    has_outbox = (
        EmailOutbox.objects.filter(is_enabled=True).exclude(host="").exists()
    )
    if has_outbox:
        return True

    backend_path = getattr(settings, "EMAIL_BACKEND", "")
    if not backend_path:
        return False
    try:
        backend_cls = import_string(backend_path)
    except Exception:  # pragma: no cover - misconfigured backend
        logger.warning("Email backend %s could not be imported", backend_path)
        return False

    if DummyEmailBackend is None:
        return True
    try:
        return not issubclass(backend_cls, DummyEmailBackend)
    except TypeError:  # pragma: no cover - backend not a class
        logger.warning("Email backend %s is not a class", backend_path)
        return False
