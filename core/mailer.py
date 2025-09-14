import logging
from typing import Sequence

from django.conf import settings
from django.core.mail import EmailMessage

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
    email.send(fail_silently=fail_silently)
    return email
