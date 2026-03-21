"""Regression tests for the verb-based email management command."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.emails.models import EmailBridge, EmailInbox, EmailOutbox


@pytest.fixture()
def owner(db):
    """Create a reusable profile owner for email command tests."""

    return get_user_model().objects.create_user(
        username="email-command-owner",
        email="email-command-owner@example.com",
    )


def test_email_inbox_subcommand_creates_and_reports_inbox(owner):
    """The inbox subcommand should create an inbox and support positional lookup."""

    create_stdout = StringIO()
    call_command(
        "email",
        "inbox",
        "--owner-user",
        str(owner.pk),
        "--username",
        "alerts@example.com",
        "--host",
        "imap.example.com",
        "--port",
        "993",
        "--protocol",
        EmailInbox.IMAP,
        "--password",
        "secret",
        "--priority",
        "7",
        "--ssl",
        stdout=create_stdout,
    )

    inbox = EmailInbox.objects.get(username="alerts@example.com")
    assert f"Configured inbox #{inbox.pk}" in create_stdout.getvalue()
    assert inbox.use_ssl is True

    report_stdout = StringIO()
    call_command("email", "inbox", str(inbox.pk), stdout=report_stdout)
    payload = json.loads(report_stdout.getvalue())
    assert payload == [
        {
            "host": "imap.example.com",
            "id": inbox.pk,
            "is_enabled": True,
            "owner": owner.username,
            "port": 993,
            "priority": 7,
            "protocol": EmailInbox.IMAP,
            "use_ssl": True,
            "username": "alerts@example.com",
        }
    ]


def test_email_outbox_subcommand_updates_positional_outbox(owner):
    """The outbox subcommand should update the selected outbox via positional id."""

    outbox = EmailOutbox.objects.create(
        user=owner,
        host="smtp.old.example.com",
        port=587,
        username="old@example.com",
        password="old-pass",
        from_email="old@example.com",
    )

    stdout = StringIO()
    call_command(
        "email",
        "outbox",
        str(outbox.pk),
        "--host",
        "smtp.example.com",
        "--from",
        "ops@example.com",
        "--tls",
        "--priority",
        "4",
        stdout=stdout,
    )

    outbox.refresh_from_db()
    assert f"Configured outbox #{outbox.pk}" in stdout.getvalue()
    assert outbox.host == "smtp.example.com"
    assert outbox.from_email == "ops@example.com"
    assert outbox.use_tls is True
    assert outbox.priority == 4


def test_email_bridge_subcommand_creates_and_reports_bridge(owner):
    """The bridge subcommand should keep bridge-specific options grouped together."""

    inbox = EmailInbox.objects.create(
        user=owner,
        username="bridge-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
    )
    outbox = EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username="bridge-outbox@example.com",
        password="secret",
        from_email="bridge-outbox@example.com",
    )

    create_stdout = StringIO()
    call_command(
        "email",
        "bridge",
        "--name",
        "Primary bridge",
        "--inbox",
        str(inbox.pk),
        "--outbox",
        str(outbox.pk),
        stdout=create_stdout,
    )

    bridge = EmailBridge.objects.get(name="Primary bridge")
    assert f"Configured bridge #{bridge.pk}" in create_stdout.getvalue()

    report_stdout = StringIO()
    call_command("email", "bridge", str(bridge.pk), stdout=report_stdout)
    payload = json.loads(report_stdout.getvalue())
    assert payload == [
        {
            "id": bridge.pk,
            "inbox_id": inbox.pk,
            "name": "Primary bridge",
            "outbox_id": outbox.pk,
        }
    ]


def test_email_send_subcommand_uses_short_aliases(monkeypatch, owner):
    """The send subcommand should support the short recipient, subject, message, and from flags."""

    outbox = EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username="sender@example.com",
        password="secret",
        from_email="sender@example.com",
    )
    sent = {}

    def fake_send(subject, message, recipients, from_email=None, outbox=None, fail_silently=False):
        sent.update(
            {
                "subject": subject,
                "message": message,
                "recipients": recipients,
                "from_email": from_email,
                "outbox": outbox,
                "fail_silently": fail_silently,
            }
        )

    monkeypatch.setattr("apps.emails.mailer.send", fake_send)

    stdout = StringIO()
    call_command(
        "email",
        "send",
        str(outbox.pk),
        "-t",
        "alice@example.com,bob@example.com",
        "-s",
        "Deploy complete",
        "-m",
        "Everything shipped.",
        "-f",
        "ops@example.com",
        stdout=stdout,
    )

    assert "Sent email to alice@example.com, bob@example.com" in stdout.getvalue()
    assert sent == {
        "subject": "Deploy complete",
        "message": "Everything shipped.",
        "recipients": ["alice@example.com", "bob@example.com"],
        "from_email": "ops@example.com",
        "outbox": outbox,
        "fail_silently": False,
    }


def test_email_search_subcommand_uses_aliases(monkeypatch, owner):
    """The search subcommand should route filters through the selected inbox."""

    inbox = EmailInbox.objects.create(
        user=owner,
        username="search@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
    )

    def fake_search_messages(**kwargs):
        assert kwargs == {
            "subject": "invoice",
            "from_address": "billing@example.com",
            "body": "paid",
            "limit": 2,
            "use_regular_expressions": True,
        }
        return [{"subject": "invoice", "from": "billing@example.com"}]

    monkeypatch.setattr(EmailInbox, "search_messages", lambda self, **kwargs: fake_search_messages(**kwargs))

    stdout = StringIO()
    call_command(
        "email",
        "search",
        str(inbox.pk),
        "-s",
        "invoice",
        "-f",
        "billing@example.com",
        "-b",
        "paid",
        "-n",
        "2",
        "-r",
        stdout=stdout,
    )

    assert json.loads(stdout.getvalue()) == [{"from": "billing@example.com", "subject": "invoice"}]


def test_email_list_subcommand_reports_all_sections(owner):
    """The list subcommand should emit the grouped report output."""

    inbox = EmailInbox.objects.create(
        user=owner,
        username="list-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
    )
    outbox = EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username="list-outbox@example.com",
        password="secret",
        from_email="list-outbox@example.com",
    )
    EmailBridge.objects.create(name="Listed bridge", inbox=inbox, outbox=outbox)

    stdout = StringIO()
    call_command("email", "list", stdout=stdout)
    payload = json.loads(stdout.getvalue())

    assert payload["inboxes"][0]["id"] == inbox.pk
    assert payload["outboxes"][0]["id"] == outbox.pk
    assert payload["bridges"][0]["name"] == "Listed bridge"


def test_email_legacy_flat_flags_remain_supported(monkeypatch, owner):
    """The legacy flat send/search flags should continue to work as a compatibility path."""

    inbox = EmailInbox.objects.create(
        user=owner,
        username="legacy-search@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
    )
    results = [{"subject": "legacy"}]
    monkeypatch.setattr(EmailInbox, "search_messages", lambda self, **kwargs: results)

    stdout = StringIO()
    call_command(
        "email",
        "--search",
        "--inbox",
        str(inbox.pk),
        "--subject",
        "legacy",
        stdout=stdout,
    )

    assert json.loads(stdout.getvalue()) == results


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("search", "-n", "0"), "--limit must be a positive integer."),
        (("send",), "send requires --to with at least one recipient."),
    ],
)
def test_email_subcommand_validation_errors(args, message):
    """The command should keep validation failures specific to each action."""

    with pytest.raises(CommandError, match=message):
        call_command("email", *args)


def test_email_send_subcommand_accepts_trailing_django_base_options(monkeypatch, owner):
    """The send subcommand should keep Django base options valid after verb arguments."""

    outbox = EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username="sender@example.com",
        password="secret",
        from_email="sender@example.com",
    )
    sent = {}

    def fake_send(subject, message, recipients, from_email=None, outbox=None, fail_silently=False):
        sent.update(
            {
                "subject": subject,
                "message": message,
                "recipients": recipients,
                "from_email": from_email,
                "outbox": outbox,
                "fail_silently": fail_silently,
            }
        )

    monkeypatch.setattr("apps.emails.mailer.send", fake_send)

    stdout = StringIO()
    call_command(
        "email",
        "send",
        str(outbox.pk),
        "-t",
        "alice@example.com",
        "--verbosity",
        "2",
        stdout=stdout,
    )

    assert "Sent email to alice@example.com" in stdout.getvalue()
    assert sent["outbox"] == outbox
    assert sent["recipients"] == ["alice@example.com"]


def test_email_legacy_bridge_selector_reports_requested_bridge(owner):
    """The legacy bridge selector should still validate and filter bridge reports."""

    inbox = EmailInbox.objects.create(
        user=owner,
        username="legacy-bridge-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
    )
    outbox = EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username="legacy-bridge-outbox@example.com",
        password="secret",
        from_email="legacy-bridge-outbox@example.com",
    )
    bridge = EmailBridge.objects.create(name="Legacy bridge", inbox=inbox, outbox=outbox)

    stdout = StringIO()
    call_command("email", "--bridge", str(bridge.pk), stdout=stdout)

    assert json.loads(stdout.getvalue()) == [
        {
            "id": bridge.pk,
            "inbox_id": inbox.pk,
            "name": "Legacy bridge",
            "outbox_id": outbox.pk,
        }
    ]


def test_email_legacy_bridge_selector_raises_for_missing_bridge(db):
    """The legacy bridge selector should keep the not-found validation behavior."""

    with pytest.raises(CommandError, match="Bridge not found: 999999"):
        call_command("email", "--bridge", "999999")
