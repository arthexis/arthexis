import pytest
from django.contrib.contenttypes.models import ContentType

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
