from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.nodes.models import NetMessage, Node, NodeRole
from apps.sigils.models import SigilRoot


@pytest.mark.django_db
def test_net_message_payload_resolves_sigils(monkeypatch):
    SigilRoot.objects.update_or_create(
        prefix="NODE",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
        },
    )

    role = NodeRole.objects.create(name="Gateway")
    node = Node.objects.create(
        hostname="gway-001",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="gway-001",
        role=role,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    message = NetMessage.objects.create(
        subject="Role: [NODE.ROLE]",
        body="Host: [NODE.hostname]",
        node_origin=node,
    )

    payload = message._build_payload(
        sender_id=str(node.uuid),
        origin_uuid=str(node.uuid),
        reach_name=None,
        seen=[],
    )

    assert payload["subject"] == f"Role: {role.name}"
    assert payload["body"] == f"Host: {node.hostname}"


@pytest.mark.django_db
def test_expired_net_message_skips_propagation(monkeypatch):
    role = NodeRole.objects.create(name="Terminal")
    node = Node.objects.create(
        hostname="local",  # pragma: allowlist secret
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local",
        role=role,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    notify_called = False

    def _notify(*args, **kwargs):
        nonlocal notify_called
        notify_called = True
        return True

    monkeypatch.setattr("apps.core.notifications.notify", _notify)

    message = NetMessage.objects.create(
        subject="Expired",
        body="Skip",
        node_origin=node,
        expires_at=timezone.now() - timedelta(minutes=5),
    )

    message.propagate()

    assert notify_called is False
    message.refresh_from_db()
    assert message.complete is True


@pytest.mark.django_db
def test_net_message_passes_expiration_to_notify(monkeypatch):
    role = NodeRole.objects.create(name="Terminal")
    node = Node.objects.create(
        hostname="local",  # pragma: allowlist secret
        address="127.0.0.1",
        mac_address="00:11:22:33:44:66",
        port=8888,
        public_endpoint="local",
        role=role,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    captured: dict[str, object] = {}

    def _notify(subject, body, *, sticky=False, expires_at=None):
        captured.update(
            {
                "subject": subject,
                "body": body,
                "sticky": sticky,
                "expires_at": expires_at,
            }
        )
        return True

    monkeypatch.setattr("apps.core.notifications.notify", _notify)

    expiration = timezone.now() + timedelta(hours=1)
    message = NetMessage.objects.create(
        subject="Hello",
        body="World",
        node_origin=node,
        expires_at=expiration,
    )

    message.propagate()

    assert captured["expires_at"] == expiration
