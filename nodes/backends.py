from __future__ import annotations

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail import get_connection
from django.conf import settings
from django.db.models import Q

from .models import EmailOutbox


class OutboxEmailBackend(BaseEmailBackend):
    """Email backend that selects an :class:`EmailOutbox` automatically.

    If a matching outbox exists for the message's ``from_email`` (matching
    either ``from_email`` or ``username``), that outbox's SMTP credentials are
    used. Otherwise, the first available outbox is used. When no outboxes are
    configured, the system falls back to Django's default SMTP settings.
    """

    def _select_outbox(self, from_email: str | None) -> EmailOutbox | None:
        if from_email:
            return (
                EmailOutbox.objects.filter(
                    Q(from_email__iexact=from_email) | Q(username__iexact=from_email)
                ).first()
                or EmailOutbox.objects.first()
            )
        return EmailOutbox.objects.first()

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            outbox = self._select_outbox(message.from_email)
            if outbox:
                connection = outbox.get_connection()
                if not message.from_email:
                    message.from_email = (
                        outbox.from_email or settings.DEFAULT_FROM_EMAIL
                    )
            else:
                connection = get_connection(
                    "django.core.mail.backends.smtp.EmailBackend"
                )
                if not message.from_email:
                    message.from_email = settings.DEFAULT_FROM_EMAIL
            try:
                sent += connection.send_messages([message]) or 0
            finally:
                try:
                    connection.close()
                except Exception:  # pragma: no cover - close errors shouldn't fail send
                    pass
        return sent
