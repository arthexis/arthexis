from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.emails.models import EmailInbox

pytestmark = pytest.mark.django_db


def _create_inbox(username: str, *, password: str = "old-secret") -> EmailInbox:
    owner = get_user_model().objects.create_user(
        username=f"owner-{EmailInbox.objects.count()}"
    )
    return EmailInbox.objects.create(
        user=owner,
        username=username,
        host="imap.example.com",
        port=993,
        password=password,
        protocol=EmailInbox.IMAP,
        use_ssl=True,
        is_enabled=True,
    )


def test_email_inbox_set_password_resolves_existing_username() -> None:
    inbox = _create_inbox("tecnologia@gelectriic.com")
    stdout = io.StringIO()

    call_command(
        "email",
        "inbox",
        "tecnologia@gelectriic.com",
        "--set-password",
        "new-secret",
        stdout=stdout,
    )

    inbox.refresh_from_db()
    assert inbox.password == "new-secret"
    assert EmailInbox.objects.count() == 1
    assert f"Configured inbox #{inbox.pk}" in stdout.getvalue()


def test_email_inbox_set_password_fails_for_missing_username() -> None:
    with pytest.raises(
        CommandError, match="No inbox found for username/email 'missing@example.com'"
    ):
        call_command(
            "email",
            "inbox",
            "missing@example.com",
            "--set-password",
            "new-secret",
        )

    assert EmailInbox.objects.count() == 0


def test_email_inbox_set_password_fails_for_ambiguous_username() -> None:
    first = _create_inbox("duplicate@example.com")
    second = _create_inbox("duplicate@example.com")

    with pytest.raises(
        CommandError,
        match=rf"Multiple inboxes match username/email 'duplicate@example.com': {first.pk}, {second.pk}",
    ):
        call_command(
            "email",
            "inbox",
            "duplicate@example.com",
            "--set-password",
            "new-secret",
        )

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.password == "old-secret"
    assert second.password == "old-secret"


def test_email_inbox_id_password_update_still_works() -> None:
    inbox = _create_inbox("id-compat@example.com")

    call_command("email", "inbox", str(inbox.pk), "--password", "id-secret")

    inbox.refresh_from_db()
    assert inbox.password == "id-secret"


def test_email_legacy_inbox_set_password_resolves_existing_username() -> None:
    inbox = _create_inbox("legacy@example.com")

    call_command(
        "email", "--inbox", "legacy@example.com", "--set-password", "legacy-secret"
    )

    inbox.refresh_from_db()
    assert inbox.password == "legacy-secret"


def test_email_inbox_set_password_env_reads_secret(monkeypatch) -> None:
    inbox = _create_inbox("env@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "env-secret")

    call_command(
        "email", "inbox", "env@example.com", "--set-password-env", "EMAIL_PASSWORD"
    )

    inbox.refresh_from_db()
    assert inbox.password == "env-secret"
