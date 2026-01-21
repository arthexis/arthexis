import pytest
from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError

from apps.emails.models import EmailBridge, EmailInbox, EmailOutbox


pytestmark = pytest.mark.django_db


def _create_owner(username: str):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="password",
    )


def _create_inbox(owner, username: str):
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


def _create_outbox(owner, username: str):
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


def test_bridge_str_prefers_trimmed_name():
    owner = _create_owner("bridge-owner")
    inbox = _create_inbox(owner, "inbox@example.com")
    outbox = _create_outbox(owner, "outbox@example.com")

    bridge = EmailBridge.objects.create(
        name="  Bridge Name  ",
        inbox=inbox,
        outbox=outbox,
    )

    assert str(bridge) == "Bridge Name"


def test_bridge_str_falls_back_to_inbox_outbox():
    owner = _create_owner("bridge-owner-2")
    inbox = _create_inbox(owner, "fallback-inbox@example.com")
    outbox = _create_outbox(owner, "fallback-outbox@example.com")

    bridge = EmailBridge.objects.create(
        name="   ",
        inbox=inbox,
        outbox=outbox,
    )

    assert str(bridge) == "fallback-inbox@example.com ↔ fallback-outbox@example.com"


def test_bridge_protects_related_inbox_and_outbox():
    owner = _create_owner("bridge-owner-3")
    inbox = _create_inbox(owner, "protect-inbox@example.com")
    outbox = _create_outbox(owner, "protect-outbox@example.com")

    EmailBridge.objects.create(inbox=inbox, outbox=outbox)

    with pytest.raises(ProtectedError):
        inbox.delete()

    with pytest.raises(ProtectedError):
        outbox.delete()
