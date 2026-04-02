from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.nodes.models import Node
from apps.nodes.services import sibling_ipc


@pytest.mark.django_db
def test_registration_falls_back_to_public_key_lookup_when_mac_missing(monkeypatch):
    sender = Node.objects.create(
        hostname="sender",
        current_relation=Node.Relation.SIBLING,
        public_key="pub-key-value",
    )

    monkeypatch.setattr(
        sibling_ipc,
        "register_node",
        lambda request: SimpleNamespace(status_code=200),
    )

    response = sibling_ipc.handle_operation(
        "registration",
        {"public_key": sender.public_key},
    )

    assert response == {"ok": True, "status": 200}


@pytest.mark.django_db
def test_net_message_requires_valid_signature(monkeypatch):
    sender = Node.objects.create(
        hostname="sender",
        current_relation=Node.Relation.SIBLING,
        uuid=uuid.uuid4(),
        public_key="pub-key-value",
    )

    called: list[dict[str, object]] = []
    monkeypatch.setattr(
        sibling_ipc.NetMessage,
        "receive_payload",
        lambda payload, sender=None: called.append(payload),
    )

    response = sibling_ipc.handle_operation(
        "net_message",
        {
            "payload": {"sender": str(sender.uuid), "subject": "hello"},
            "signature": "not-valid-base64",
        },
    )

    assert response == {"ok": False, "detail": "invalid signature"}
    assert not called
