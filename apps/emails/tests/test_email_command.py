"""Tests for the email management command."""

from __future__ import annotations

import io
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.core.management import call_command

from apps.emails.models import EmailBridge, EmailInbox, EmailOutbox


pytestmark = pytest.mark.django_db


def _create_owner(username: str):
    """Create and return a basic user for ownership tests."""

    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="password",
    )


def _create_inbox(owner, username: str) -> EmailInbox:
    """Create an inbox profile for tests."""

    return EmailInbox.objects.create(
        user=owner,
        username=username,
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
        use_ssl=True,
        is_enabled=True,
        priority=1,
    )


def _create_outbox(owner, username: str) -> EmailOutbox:
    """Create an outbox profile for tests."""

    return EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username=username,
        password="secret",
        use_tls=True,
        use_ssl=False,
        from_email=username,
        is_enabled=True,
        priority=1,
    )


def test_email_command_reports_configurations():
    """Calling the command with no flags should report inbox/outbox/bridge data."""

    owner = _create_owner("report-owner")
    inbox = _create_inbox(owner, "report-inbox@example.com")
    outbox = _create_outbox(owner, "report-outbox@example.com")
    EmailBridge.objects.create(name="bridge-a", inbox=inbox, outbox=outbox)

    stdout = io.StringIO()
    call_command("email", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["inboxes"][0]["username"] == "report-inbox@example.com"
    assert payload["outboxes"][0]["username"] == "report-outbox@example.com"
    assert payload["bridges"][0]["name"] == "bridge-a"


def test_email_command_configures_profiles_and_bridge():
    """Configuration flags should create inbox/outbox and bridge records."""

    owner = _create_owner("config-owner")
    stdout = io.StringIO()

    call_command(
        "email",
        "--owner-user",
        str(owner.pk),
        "--inbox-username",
        "cli-inbox@example.com",
        "--inbox-host",
        "imap.cli.example.com",
        "--inbox-password",
        "secret",
        stdout=stdout,
    )
    inbox = EmailInbox.objects.get(username="cli-inbox@example.com")

    call_command(
        "email",
        "--owner-user",
        str(owner.pk),
        "--outbox-host",
        "smtp.cli.example.com",
        "--outbox-username",
        "cli-outbox@example.com",
        "--outbox-password",
        "secret",
        "--outbox-from-email",
        "cli-outbox@example.com",
        stdout=stdout,
    )
    outbox = EmailOutbox.objects.get(username="cli-outbox@example.com")

    call_command(
        "email",
        "--bridge-name",
        "cli bridge",
        "--bridge-inbox",
        str(inbox.pk),
        "--bridge-outbox",
        str(outbox.pk),
        stdout=stdout,
    )

    bridge = EmailBridge.objects.get(name="cli bridge")
    assert bridge.inbox_id == inbox.pk
    assert bridge.outbox_id == outbox.pk


def test_email_command_send_uses_mailer(monkeypatch):
    """Send flags should delegate to apps.emails.mailer.send."""

    owner = _create_owner("send-owner")
    outbox = _create_outbox(owner, "send-outbox@example.com")
    captured: dict[str, object] = {}

    def _fake_send(subject, message, recipients, **kwargs):
        captured["subject"] = subject
        captured["message"] = message
        captured["recipients"] = recipients
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr("apps.emails.mailer.send", _fake_send)

    call_command(
        "email",
        "--send",
        "--outbox",
        str(outbox.pk),
        "--to",
        "alpha@example.com,beta@example.com",
        "--subject",
        "CLI Subject",
        "--message",
        "CLI Body",
    )

    assert captured["subject"] == "CLI Subject"
    assert captured["message"] == "CLI Body"
    assert captured["recipients"] == ["alpha@example.com", "beta@example.com"]
    assert captured["kwargs"]["outbox"].pk == outbox.pk


def test_email_command_search_uses_inbox(monkeypatch):
    """Search flags should use inbox.search_messages and print JSON results."""

    owner = _create_owner("search-owner")
    inbox = _create_inbox(owner, "search-inbox@example.com")

    def _fake_search_messages(*, subject, from_address, body, limit, use_regular_expressions):
        return [
            {
                "subject": subject,
                "from": from_address,
                "body": body,
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "limit": limit,
                "regex": use_regular_expressions,
            }
        ]

    monkeypatch.setattr(inbox, "search_messages", _fake_search_messages)
    monkeypatch.setattr(
        "apps.emails.management.commands.email.EmailInbox.objects.get",
        lambda *args, **kwargs: inbox,
    )

    stdout = io.StringIO()
    call_command(
        "email",
        "--search",
        "--inbox",
        str(inbox.pk),
        "--subject",
        "match me",
        "--search-from",
        "sender@example.com",
        "--search-body",
        "needle",
        "--search-limit",
        "3",
        "--regex",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload[0]["subject"] == "match me"
    assert payload[0]["from"] == "sender@example.com"
    assert payload[0]["body"] == "needle"
    assert payload[0]["limit"] == 3
    assert payload[0]["regex"] is True


def test_email_command_inbox_priority_can_be_reset_to_zero():
    """Inbox updates should treat --inbox-priority 0 as a valid change."""

    owner = _create_owner("priority-owner")
    inbox = _create_inbox(owner, "priority-inbox@example.com")
    inbox.priority = 5
    inbox.save(update_fields=["priority"])

    call_command(
        "email",
        "--inbox",
        str(inbox.pk),
        "--inbox-priority",
        "0",
    )

    inbox.refresh_from_db()
    assert inbox.priority == 0


def test_email_command_send_from_email_overrides_outbox(monkeypatch):
    """--from-email should override the selected outbox sender address."""

    owner = _create_owner("send-override-owner")
    outbox = _create_outbox(owner, "send-override-outbox@example.com")
    captured: dict[str, object] = {}

    def _fake_send(subject, message, recipients, **kwargs):
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr("apps.emails.mailer.send", _fake_send)

    call_command(
        "email",
        "--send",
        "--outbox",
        str(outbox.pk),
        "--to",
        "alpha@example.com",
        "--from-email",
        "override@example.com",
    )

    assert captured["kwargs"]["from_email"] == "override@example.com"
    assert captured["kwargs"]["outbox"].from_email == "override@example.com"


def test_email_command_search_rejects_non_positive_limit():
    """Search should reject non-positive limits to keep message slicing predictable."""

    with pytest.raises(CommandError, match="--search-limit must be a positive integer"):
        call_command("email", "--search", "--search-limit", "0")
