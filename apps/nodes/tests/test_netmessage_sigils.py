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
def test_net_message_payload_includes_expiration(monkeypatch):
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

    expires_at = timezone.now().replace(microsecond=0) + timedelta(hours=1)
    message = NetMessage.objects.create(
        subject="Role: [NODE.ROLE]",
        body="Host: [NODE.hostname]",
        node_origin=node,
        expires_at=expires_at,
    )

    payload = message._build_payload(
        sender_id=str(node.uuid),
        origin_uuid=str(node.uuid),
        reach_name=None,
        seen=[],
    )

    assert payload.get("expires_at") == expires_at.isoformat()


@pytest.mark.django_db
def test_net_message_propagation_skips_when_expired(monkeypatch):
    expired_at = timezone.now() - timedelta(minutes=5)
    message = NetMessage.objects.create(subject="Expired", expires_at=expired_at)

    notified = False

    def _notify(*args, **kwargs):
        nonlocal notified
        notified = True
        return True

    monkeypatch.setattr(
        "apps.core.notifications.notify",
        _notify,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: None))

    message.propagate()

    assert notified is False
    assert message.complete is True
