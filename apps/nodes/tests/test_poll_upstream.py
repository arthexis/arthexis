import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone

import pytest

from apps.nodes.models import Node
from apps.nodes.tasks import poll_upstream


@pytest.mark.django_db
def test_poll_upstream_updates_last_updated_on_success(monkeypatch):
    local_mac = "aa:bb:cc:dd:ee:ff"
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: local_mac))

    local_node = Node.objects.create(
        hostname="local",
        mac_address=local_mac,
        address="127.0.0.1",
        port=8888,
        public_endpoint="local-node",
        current_relation=Node.Relation.SELF,
    )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    upstream_node = Node.objects.create(
        hostname="upstream",
        mac_address="11:22:33:44:55:66",
        address="198.51.100.10",
        port=8888,
        public_endpoint="upstream-node",
        public_key=public_key.decode(),
        current_relation=Node.Relation.UPSTREAM,
    )

    monkeypatch.setattr(
        Node,
        "has_feature",
        lambda self, slug: slug == "celery-queue",
    )
    monkeypatch.setattr(Node, "get_private_key", lambda self: private_key)
    monkeypatch.setattr(
        Node,
        "iter_remote_urls",
        lambda self, path: [f"https://upstream.example{path}"],
    )

    class DummyResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"messages": []}

    def fake_post(url, data, headers, timeout):
        payload = json.loads(data)
        assert payload["requester"] == str(local_node.uuid)
        assert "X-Signature" in headers
        return DummyResponse()

    monkeypatch.setattr("apps.nodes.tasks.requests.post", fake_post)

    expected_timestamp = upstream_node.last_updated + timezone.timedelta(seconds=1)
    monkeypatch.setattr("apps.nodes.tasks.django_timezone.now", lambda: expected_timestamp)
    poll_upstream()
    upstream_node.refresh_from_db()

    assert upstream_node.last_updated == expected_timestamp
