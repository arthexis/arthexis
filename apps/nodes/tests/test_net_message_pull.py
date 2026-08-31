import base64
import json
import uuid
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import RequestFactory
from django.utils import timezone

from apps.nodes.models import NetMessage, Node, PendingNetMessage
from apps.nodes.models.net_message import LEGACY_REMOTE_UPGRADE_CONTROL_QUEUE_MARKER
from apps.nodes.views.network import net_message_pull


@pytest.mark.django_db
def test_net_message_pull_accepts_trusted_key_hints_with_stale_requester_uuid(monkeypatch):
    requester_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requester_public_key = requester_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    stale_uuid = uuid.uuid4()
    live_uuid = uuid.uuid4()

    requester_node = Node.objects.create(
        uuid=stale_uuid,
        hostname="downstream",
        mac_address="aa:bb:cc:dd:ee:20",
        address="198.51.100.20",
        port=8888,
        public_key=requester_public_key,
    )

    local_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    local_public_key = local_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    local_node = Node.objects.create(
        hostname="self-node",
        mac_address="aa:bb:cc:dd:ee:99",
        address="198.51.100.99",
        port=8888,
        public_key=local_public_key,
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    monkeypatch.setattr(local_node, "get_private_key", lambda: local_private_key)

    payload = {
        "requester": str(live_uuid),
        "requester_mac": requester_node.mac_address,
        "requester_public_key": requester_public_key,
    }
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = requester_private_key.sign(
        raw_payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    request = RequestFactory().post(
        "/nodes/net-message/pull/",
        data=raw_payload,
        content_type="application/json",
        headers={"X-Signature": base64.b64encode(signature).decode()},
    )

    response = net_message_pull(request)

    requester_node.refresh_from_db()
    assert response.status_code == 200
    assert json.loads(response.content.decode()) == {"messages": []}
    assert requester_node.uuid == live_uuid


@pytest.mark.django_db
def test_net_message_pull_drops_queued_remote_upgrade_controls(monkeypatch):
    requester_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requester_public_key = requester_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    requester_node = Node.objects.create(
        hostname="downstream",
        mac_address="aa:bb:cc:dd:ee:21",
        address="198.51.100.21",
        port=8888,
        public_key=requester_public_key,
    )

    local_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    local_public_key = local_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    local_node = Node.objects.create(
        hostname="self-node",
        mac_address="aa:bb:cc:dd:ee:98",
        address="198.51.100.98",
        port=8888,
        public_key=local_public_key,
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    monkeypatch.setattr(local_node, "get_private_key", lambda: local_private_key)

    message = NetMessage.objects.create(
        subject="Remote upgrade request",
        body="stable",
        kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        control_payload={"remote_upgrade_request": {"uuid": str(requester_node.uuid)}},
        node_origin=local_node,
        complete=False,
    )
    pending = PendingNetMessage.objects.create(
        node=requester_node,
        message=message,
        seen=[str(local_node.uuid), str(requester_node.uuid)],
        stale_at=timezone.now() + timedelta(hours=1),
    )

    payload = {"requester": str(requester_node.uuid)}
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = requester_private_key.sign(
        raw_payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    request = RequestFactory().post(
        "/nodes/net-message/pull/",
        data=raw_payload,
        content_type="application/json",
        headers={"X-Signature": base64.b64encode(signature).decode()},
    )

    response = net_message_pull(request)

    message.refresh_from_db()
    assert response.status_code == 200
    assert json.loads(response.content.decode()) == {"messages": []}
    assert message.complete is True
    assert not PendingNetMessage.objects.filter(pk=pending.pk).exists()


@pytest.mark.django_db
def test_net_message_pull_delivers_flagged_local_remote_upgrade_controls(monkeypatch):
    requester_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requester_public_key = requester_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    requester_node = Node.objects.create(
        hostname="downstream",
        mac_address="aa:bb:cc:dd:ee:22",
        address="198.51.100.22",
        port=8888,
        public_key=requester_public_key,
    )

    local_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    local_public_key = local_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    local_node = Node.objects.create(
        hostname="self-node",
        mac_address="aa:bb:cc:dd:ee:97",
        address="198.51.100.97",
        port=8888,
        public_key=local_public_key,
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    monkeypatch.setattr(local_node, "get_private_key", lambda: local_private_key)

    message = NetMessage.objects.create(
        subject="Remote upgrade request",
        body="stable",
        kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        control_payload={"remote_upgrade_request": {"uuid": str(requester_node.uuid)}},
        node_origin=local_node,
        complete=False,
    )
    pending = PendingNetMessage.objects.create(
        node=requester_node,
        message=message,
        seen=[
            str(local_node.uuid),
            str(requester_node.uuid),
            LEGACY_REMOTE_UPGRADE_CONTROL_QUEUE_MARKER,
        ],
        local_remote_upgrade_control=True,
        stale_at=timezone.now() + timedelta(hours=1),
    )

    payload = {"requester": str(requester_node.uuid)}
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = requester_private_key.sign(
        raw_payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    request = RequestFactory().post(
        "/nodes/net-message/pull/",
        data=raw_payload,
        content_type="application/json",
        headers={"X-Signature": base64.b64encode(signature).decode()},
    )

    response = net_message_pull(request)
    response_payload = json.loads(response.content.decode())

    assert response.status_code == 200
    assert len(response_payload["messages"]) == 1
    delivered_payload = response_payload["messages"][0]["payload"]
    assert delivered_payload["kind"] == NetMessage.Kind.REMOTE_UPGRADE_REQUEST
    assert delivered_payload["sender"] == str(local_node.uuid)
    assert LEGACY_REMOTE_UPGRADE_CONTROL_QUEUE_MARKER not in delivered_payload["seen"]
    assert not PendingNetMessage.objects.filter(pk=pending.pk).exists()


@pytest.mark.django_db
def test_net_message_pull_drops_stale_seen_marker_without_local_flag(monkeypatch):
    requester_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requester_public_key = requester_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    requester_node = Node.objects.create(
        hostname="downstream",
        mac_address="aa:bb:cc:dd:ee:23",
        address="198.51.100.23",
        port=8888,
        public_key=requester_public_key,
    )

    local_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    local_public_key = local_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    local_node = Node.objects.create(
        hostname="self-node",
        mac_address="aa:bb:cc:dd:ee:96",
        address="198.51.100.96",
        port=8888,
        public_key=local_public_key,
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    monkeypatch.setattr(local_node, "get_private_key", lambda: local_private_key)

    message = NetMessage.objects.create(
        subject="Remote upgrade request",
        body="stable",
        kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        control_payload={"remote_upgrade_request": {"uuid": str(requester_node.uuid)}},
        node_origin=local_node,
        complete=False,
    )
    pending = PendingNetMessage.objects.create(
        node=requester_node,
        message=message,
        seen=[
            str(local_node.uuid),
            str(requester_node.uuid),
            LEGACY_REMOTE_UPGRADE_CONTROL_QUEUE_MARKER,
        ],
        stale_at=timezone.now() + timedelta(hours=1),
    )

    payload = {"requester": str(requester_node.uuid)}
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = requester_private_key.sign(
        raw_payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    request = RequestFactory().post(
        "/nodes/net-message/pull/",
        data=raw_payload,
        content_type="application/json",
        headers={"X-Signature": base64.b64encode(signature).decode()},
    )

    response = net_message_pull(request)

    message.refresh_from_db()
    assert response.status_code == 200
    assert json.loads(response.content.decode()) == {"messages": []}
    assert message.complete is True
    assert not PendingNetMessage.objects.filter(pk=pending.pk).exists()
