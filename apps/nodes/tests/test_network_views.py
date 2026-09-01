from __future__ import annotations

import json
import logging
import uuid

import pytest
from django.test import RequestFactory

from apps.nodes.models import NetMessage, Node
from apps.nodes.views import network


@pytest.mark.django_db
def test_net_message_logs_validation_reason_without_exposing_it_to_peer(monkeypatch, caplog):
    sender = Node.objects.create(
        hostname="sender",
        current_relation=Node.Relation.SIBLING,
        uuid=uuid.uuid4(),
        public_key="test-public-key",
    )

    class PublicKey:
        def verify(self, *_args) -> None:
            return None

    def reject_payload(*_args, **_kwargs) -> None:
        raise ValueError("uuid is required")

    monkeypatch.setattr(network.serialization, "load_pem_public_key", lambda _value: PublicKey())
    monkeypatch.setattr(NetMessage, "receive_payload", reject_payload)
    request = RequestFactory().post(
        "/nodes/net-message/",
        data=json.dumps({"sender": str(sender.uuid)}),
        content_type="application/json",
        HTTP_X_SIGNATURE="c2ln",
    )

    with caplog.at_level(logging.WARNING, logger="apps.nodes.views.network"):
        response = network.net_message(request)

    assert response.status_code == 400
    assert json.loads(response.content) == {"detail": "invalid message"}
    assert any("uuid is required" in message for message in caplog.messages)
