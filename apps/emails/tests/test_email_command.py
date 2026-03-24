"""Regression tests for the verb-based email management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.emails.models import EmailOutbox


@pytest.fixture()
def owner(db):
    """Create a reusable profile owner for email command tests."""

    return get_user_model().objects.create_user(
        username="email-command-owner",
        email="email-command-owner@example.com",
    )


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


def test_email_legacy_bridge_selector_raises_for_missing_bridge(db):
    """The legacy bridge selector should keep the not-found validation behavior."""

    with pytest.raises(CommandError, match="Bridge not found: 999999"):
        call_command("email", "--bridge", "999999")
