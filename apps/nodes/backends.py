from __future__ import annotations

import random
from collections import defaultdict

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail import get_connection
from django.conf import settings
from django.db.models import Q

from teams.models import EmailOutbox


class OutboxEmailBackend(BaseEmailBackend):
    """Email backend that selects an :class:`EmailOutbox` automatically.

    If a matching outbox exists for the message's ``from_email`` (matching
    either ``from_email`` or ``username``), that outbox's SMTP credentials are
    used. ``EmailOutbox`` associations to ``node``, ``user`` and ``group`` are
    also considered and preferred when multiple criteria match. When no
    outboxes are configured, the system falls back to Django's default SMTP
    settings.
    """

    def _resolve_identifier(self, message, attr: str):
        value = getattr(message, attr, None)
        if value is None:
            value = getattr(message, f"{attr}_id", None)
        if value is None:
            return None
        return getattr(value, "pk", value)

    def _select_outbox(
        self, message
    ) -> tuple[EmailOutbox | None, list[EmailOutbox]]:
        from_email = getattr(message, "from_email", None)
        node_id = self._resolve_identifier(message, "node")
        user_id = self._resolve_identifier(message, "user")
        group_id = self._resolve_identifier(message, "group")

        enabled_outboxes = EmailOutbox.objects.filter(is_enabled=True)
        match_sets: list[tuple[str, list[EmailOutbox]]] = []

        if from_email:
            email_matches = list(
                enabled_outboxes.filter(
                    Q(from_email__iexact=from_email) | Q(username__iexact=from_email)
                )
            )
            if email_matches:
                match_sets.append(("from_email", email_matches))

        if node_id:
            node_matches = list(enabled_outboxes.filter(node_id=node_id))
            if node_matches:
                match_sets.append(("node", node_matches))

        if user_id:
            user_matches = list(enabled_outboxes.filter(user_id=user_id))
            if user_matches:
                match_sets.append(("user", user_matches))

        if group_id:
            group_matches = list(enabled_outboxes.filter(group_id=group_id))
            if group_matches:
                match_sets.append(("group", group_matches))

        if not match_sets:
            fallback = self._fallback_outbox(enabled_outboxes)
            if fallback:
                return fallback, []
            return None, []

        candidates: dict[int, EmailOutbox] = {}
        scores: defaultdict[int, int] = defaultdict(int)

        for _, matches in match_sets:
            for outbox in matches:
                candidates[outbox.pk] = outbox
                scores[outbox.pk] += 1

        if not candidates:
            fallback = self._fallback_outbox(enabled_outboxes)
            if fallback:
                return fallback, []
            return None, []

        selected: EmailOutbox | None = None
        fallbacks: list[EmailOutbox] = []

        for score in sorted(set(scores.values()), reverse=True):
            group = [candidates[pk] for pk, value in scores.items() if value == score]
            if len(group) > 1:
                random.shuffle(group)
            if selected is None:
                selected = group[0]
                fallbacks.extend(group[1:])
            else:
                fallbacks.extend(group)

        return selected, fallbacks

    def _fallback_outbox(self, queryset):
        ownerless = queryset.filter(
            node__isnull=True, user__isnull=True, group__isnull=True
        ).order_by("pk").first()
        if ownerless:
            return ownerless
        return queryset.order_by("pk").first()

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            original_from_email = message.from_email
            outbox, fallbacks = self._select_outbox(message)
            tried_outboxes = []
            if outbox:
                tried_outboxes.append(outbox)
            tried_outboxes.extend(fallbacks)

            last_error: Exception | None = None

            if tried_outboxes:
                for candidate in tried_outboxes:
                    connection = candidate.get_connection()
                    message.from_email = (
                        original_from_email
                        or candidate.from_email
                        or settings.DEFAULT_FROM_EMAIL
                    )
                    try:
                        sent += connection.send_messages([message]) or 0
                        last_error = None
                        break
                    except Exception as exc:  # pragma: no cover - retry on error
                        last_error = exc
                    finally:
                        try:
                            connection.close()
                        except Exception:  # pragma: no cover - close errors shouldn't fail send
                            pass
                if last_error is not None:
                    message.from_email = original_from_email
                    raise last_error
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

            message.from_email = original_from_email
        return sent
