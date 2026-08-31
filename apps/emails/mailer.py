import logging
from typing import Iterable, Sequence

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def _record_transaction(
    *,
    outbox,
    subject: str,
    message: str,
    sender: str,
    recipient_list: Iterable[str],
    cc: Iterable[str] | None,
    bcc: Iterable[str] | None,
    status: str,
    error: str = "",
):
    try:
        from apps.core.models import EmailTransaction
    except Exception:  # pragma: no cover - allow sending before migrations
        return None

    try:
        transaction = EmailTransaction.objects.create(
            direction=EmailTransaction.OUTBOUND,
            status=status,
            outbox=outbox,
            subject=subject or "",
            from_address=sender or "",
            to_addresses=list(recipient_list),
            cc_addresses=list(cc or []),
            bcc_addresses=list(bcc or []),
            body_text=message or "",
            queued_at=timezone.now(),
            error=error,
        )
    except Exception:  # pragma: no cover - capture but do not block send
        logger.exception("Unable to record email transaction")
        return None

    return transaction


def _candidate_outboxes(user=None, node=None, outbox=None):
    candidates = []
    seen = set()

    def _add(entry):
        identifier = getattr(entry, "pk", id(entry))
        if identifier in seen:
            return
        candidates.append(entry)
        seen.add(identifier)

    if outbox is not None:
        _add(outbox)

    from apps.emails.models import EmailOutbox

    queryset = EmailOutbox.objects.filter(is_enabled=True)

    if user is not None and getattr(user, "pk", None):
        group_ids = list(user.groups.values_list("id", flat=True))
        owner_filter = Q(user_id=user.pk)
        if group_ids:
            owner_filter |= Q(group_id__in=group_ids)
        for entry in queryset.filter(owner_filter).order_by("-priority", "id"):
            _add(entry)

    target_node = node
    if target_node is None:
        try:  # pragma: no cover - Node may not be installed
            from apps.nodes.models import Node

            target_node = Node.get_local()
        except Exception:
            target_node = None

    if target_node is not None:
        for entry in queryset.filter(node=target_node).order_by("-priority", "id"):
            _add(entry)

    if not candidates:
        for entry in queryset.order_by("-priority", "id"):
            _add(entry)

    return candidates


def _build_email_message(
    *,
    subject: str,
    message: str,
    sender: str,
    recipient_list: Sequence[str],
    attachments: Sequence[tuple[str, str, str]] | None,
    content_subtype: str | None,
    connection,
    **kwargs,
):
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
    return email


def send(
    subject: str,
    message: str,
    recipient_list: Sequence[str],
    from_email: str | None = None,
    *,
    outbox=None,
    user=None,
    node=None,
    attachments: Sequence[tuple[str, str, str]] | None = None,
    content_subtype: str | None = None,
    **kwargs,
):
    """Send an email using the highest priority accessible outbox.

    When multiple outboxes are available, they are attempted in priority order. A
    transaction record is persisted for each attempt.
    """

    fail_silently = kwargs.pop("fail_silently", False)
    candidate_outboxes = _candidate_outboxes(user=user, node=node, outbox=outbox)

    default_sender = from_email or settings.DEFAULT_FROM_EMAIL

    last_error: Exception | None = None
    cc = kwargs.get("cc")
    bcc = kwargs.get("bcc")

    if not candidate_outboxes:
        transaction = _record_transaction(
            outbox=None,
            subject=subject,
            message=message,
            sender=default_sender,
            recipient_list=recipient_list,
            cc=cc,
            bcc=bcc,
            status="failed",
            error="No EmailOutbox profiles are available for sending.",
        )
        if transaction:
            transaction.processed_at = timezone.now()
            transaction.save(update_fields=["processed_at"])
        if not fail_silently:
            raise RuntimeError("No email outboxes are configured for sending")
        return None

    for candidate in candidate_outboxes:
        sender = getattr(candidate, "from_email", None) or default_sender

        try:
            connection = candidate.get_connection()
        except Exception as exc:
            logger.exception("Unable to build connection for outbox %s", candidate.pk)
            last_error = exc
            failure_txn = _record_transaction(
                outbox=candidate,
                subject=subject,
                message=message,
                sender=sender,
                recipient_list=recipient_list,
                cc=cc,
                bcc=bcc,
                status="failed",
                error=str(exc),
            )
            if failure_txn:
                failure_txn.processed_at = timezone.now()
                failure_txn.save(update_fields=["processed_at"])
            continue

        transaction = _record_transaction(
            outbox=candidate,
            subject=subject,
            message=message,
            sender=sender,
            recipient_list=recipient_list,
            cc=cc,
            bcc=bcc,
            status="queued",
        )

        try:
            email = _build_email_message(
                subject=subject,
                message=message,
                sender=sender,
                recipient_list=recipient_list,
                attachments=attachments,
                content_subtype=content_subtype,
                connection=connection,
                **kwargs,
            )
            email.send(fail_silently=False)
        except Exception as exc:
            last_error = exc
            logger.exception("Email send failed using outbox %s", candidate.pk)
            if transaction:
                transaction.status = getattr(
                    transaction, "STATUS_FAILED", "failed"
                )
                transaction.error = str(exc)
                transaction.processed_at = timezone.now()
                transaction.save(update_fields=["status", "error", "processed_at"])
            continue

        if transaction:
            transaction.status = getattr(transaction, "STATUS_SENT", "sent")
            transaction.processed_at = timezone.now()
            try:
                message_id = email.extra_headers.get("Message-ID", "")
            except Exception:
                message_id = ""
            if hasattr(transaction, "message_id"):
                transaction.message_id = message_id
                transaction.save(
                    update_fields=["status", "processed_at", "message_id"]
                )
            else:
                transaction.save(update_fields=["status", "processed_at"])
        try:
            setattr(email, "outbox", candidate)
        except Exception:
            pass
        return email

    if not fail_silently and last_error:
        raise last_error
    return None


def can_send_email() -> bool:
    """Return ``True`` when at least one outbound email path is configured."""

    try:
        from apps.emails.models import EmailOutbox  # imported lazily to avoid circular deps
    except Exception:  # pragma: no cover - app not ready
        return False

    return EmailOutbox.objects.filter(is_enabled=True).exists()
