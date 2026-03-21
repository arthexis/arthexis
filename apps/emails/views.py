"""Public inbox views for user-owned email inboxes."""

from __future__ import annotations

from collections.abc import Sequence
from email.utils import parsedate_to_datetime
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.emails.models import EmailInbox

MESSAGE_FETCH_LIMIT = 100


def _user_inboxes(request: HttpRequest):
    """Return enabled inboxes owned by the authenticated user."""

    return EmailInbox.objects.filter(user=request.user, is_enabled=True).order_by("-priority", "id")


def _resolve_selected_inbox(request: HttpRequest) -> tuple[EmailInbox | None, list[EmailInbox]]:
    """Resolve the selected inbox from the query string or fall back to the highest priority inbox."""

    inboxes = list(_user_inboxes(request))
    if not inboxes:
        return None, []

    inbox_id = (request.GET.get("inbox") or "").strip()
    if not inbox_id:
        return inboxes[0], inboxes

    try:
        selected_id = int(inbox_id)
    except ValueError as exc:
        raise ValidationError({"inbox": "Inbox selection must be a numeric id."}) from exc

    for inbox in inboxes:
        if inbox.pk == selected_id:
            return inbox, inboxes
    raise PermissionDenied("That inbox is not available for this user.")


def _parse_message_date(raw_value: str) -> tuple[int, str]:
    """Return a sortable timestamp and display string for an email date header."""

    cleaned = (raw_value or "").strip()
    if not cleaned:
        return (0, "-")
    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError, IndexError, OverflowError):
        return (0, cleaned)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.utc)
    return (int(parsed.timestamp()), timezone.localtime(parsed).strftime("%Y-%m-%d %H:%M"))


def _fetch_messages(inbox: EmailInbox, *, limit: int = MESSAGE_FETCH_LIMIT) -> list[dict[str, Any]]:
    """Fetch mailbox messages and decorate them with list/detail metadata."""

    raw_messages = inbox.search_messages(limit=limit)
    decorated_messages: list[dict[str, Any]] = []
    for index, message in enumerate(raw_messages):
        timestamp, display_date = _parse_message_date(str(message.get("date", "")))
        decorated_messages.append(
            {
                "index": index,
                "subject": str(message.get("subject", "")).strip() or "(No subject)",
                "from": str(message.get("from", "")).strip() or "Unknown sender",
                "body": str(message.get("body", "")).strip(),
                "date": str(message.get("date", "")).strip(),
                "display_date": display_date,
                "sort_timestamp": timestamp,
            }
        )
    return decorated_messages


def _message_navigation(messages: Sequence[dict[str, Any]], message_index: int) -> dict[str, int | None]:
    """Build previous/next navigation indexes for the selected message."""

    previous_index = message_index - 1 if message_index > 0 else None
    next_index = message_index + 1 if message_index + 1 < len(messages) else None
    return {"previous_index": previous_index, "next_index": next_index}


@login_required
def inbox_list(request: HttpRequest) -> HttpResponse:
    """Render a table of recent messages from the current user's selected inbox."""

    messages: list[dict[str, Any]] = []
    error_message = ""
    try:
        selected_inbox, inboxes = _resolve_selected_inbox(request)
    except ValidationError as exc:
        selected_inbox, inboxes = None, []
        error_message = "; ".join(exc.messages)
    if selected_inbox is not None:
        try:
            messages = _fetch_messages(selected_inbox)
        except ValidationError as exc:
            error_message = "; ".join(exc.messages)

    context = {
        "error_message": error_message,
        "inboxes": inboxes,
        "messages": messages,
        "selected_inbox": selected_inbox,
    }
    return render(request, "emails/inbox_list.html", context)


@login_required
def inbox_detail(request: HttpRequest, message_index: int) -> HttpResponse:
    """Render one message from the current user's selected inbox with navigation."""

    try:
        selected_inbox, inboxes = _resolve_selected_inbox(request)
    except ValidationError as exc:
        context = {
            "error_message": "; ".join(exc.messages),
            "inboxes": [],
            "selected_inbox": None,
            "message": None,
            "navigation": {"previous_index": None, "next_index": None},
        }
        return render(request, "emails/inbox_detail.html", context, status=200)
    if selected_inbox is None:
        raise Http404("No inbox is configured for this user.")

    try:
        messages = _fetch_messages(selected_inbox)
    except ValidationError as exc:
        context = {
            "error_message": "; ".join(exc.messages),
            "inboxes": inboxes,
            "selected_inbox": selected_inbox,
            "message": None,
            "navigation": {"previous_index": None, "next_index": None},
        }
        return render(request, "emails/inbox_detail.html", context, status=200)

    if message_index < 0 or message_index >= len(messages):
        raise Http404("Message was not found in the selected inbox view.")

    context = {
        "error_message": "",
        "inboxes": inboxes,
        "selected_inbox": selected_inbox,
        "message": messages[message_index],
        "navigation": _message_navigation(messages, message_index),
    }
    return render(request, "emails/inbox_detail.html", context)
