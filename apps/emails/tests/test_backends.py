from __future__ import annotations

import random
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMessage
from django.test import override_settings

from apps.emails.backends import OutboxEmailBackend
from apps.emails.models import EmailOutbox
from apps.groups.models import SecurityGroup
from apps.nodes.models import Node


@pytest.mark.django_db
def test_select_outbox_prefers_multiple_matches(monkeypatch):
    user = get_user_model().objects.create_user(username="matcher")
    group = SecurityGroup.objects.create(name="Dispatchers")
    node = Node.objects.create(hostname="edge-01")

    primary = EmailOutbox.objects.create(
        host="smtp.primary.test",
        username="primary@example.com",
        from_email="primary@example.com",
        user=user,
    )
    node_only = EmailOutbox.objects.create(
        host="smtp.node.test",
        username="node@example.com",
        node=node,
    )
    group_only = EmailOutbox.objects.create(
        host="smtp.group.test",
        username="group@example.com",
        group=group,
    )

    backend = OutboxEmailBackend()
    monkeypatch.setattr(random, "shuffle", lambda seq: None)

    message = SimpleNamespace(
        from_email="primary@example.com",
        node=node,
        user=user,
        group=group,
    )

    selected, fallbacks = backend._select_outbox(message)

    assert selected == primary
    assert node_only in fallbacks
    assert group_only in fallbacks
    assert len(fallbacks) == 2


@pytest.mark.django_db
def test_fallback_prefers_ownerless_outbox():
    ownerless = EmailOutbox.objects.create(
        host="smtp.ownerless.test", username="ownerless@example.com"
    )
    user = get_user_model().objects.create_user(username="fallback")
    EmailOutbox.objects.create(
        host="smtp.user.test", username="user@example.com", user=user
    )

    backend = OutboxEmailBackend()
    selected, fallbacks = backend._select_outbox(SimpleNamespace())

    assert selected == ownerless
    assert fallbacks == []


@pytest.mark.django_db
def test_fallback_without_ownerless_uses_first_created():
    user = get_user_model().objects.create_user(username="alpha")
    first = EmailOutbox.objects.create(
        host="smtp.alpha.test", username="alpha@example.com", user=user
    )
    group = SecurityGroup.objects.create(name="Operators")
    EmailOutbox.objects.create(
        host="smtp.beta.test", username="beta@example.com", group=group
    )

    backend = OutboxEmailBackend()
    selected, fallbacks = backend._select_outbox(SimpleNamespace())

    assert selected == first
    assert fallbacks == []


@pytest.mark.django_db
@override_settings(EMAIL_BASE_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_send_messages_routes_through_outbox_backend():
    backend = OutboxEmailBackend()
    sender = EmailOutbox.objects.create(
        host="smtp.send.test", from_email="sender@example.com", username="sender"
    )

    email = EmailMessage("Greetings", "Hello", to=["rcpt@example.com"])
    email.from_email = None

    sent = backend.send_messages([email])

    assert sent == 1
    assert len(mail.outbox) == 1
    delivered = mail.outbox[0]
    assert delivered.from_email == "sender@example.com"
