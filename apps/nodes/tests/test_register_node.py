import base64
import hashlib
import json
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.db import DatabaseError, IntegrityError
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory
from django.utils import timezone

from apps.nodes.models import Node, NodeRole
from apps.nodes.models.upgrade_policy import UpgradePolicy
from apps.nodes.services import registration
from apps.nodes.services.enrollment import issue_enrollment_token
from apps.nodes.views import next_gway_number, node_info, register_node
from apps.nodes.views.registration import handlers
from apps.sites.models import SiteProfile

GWAY_RESERVATION_TOKEN = "reservation-token"


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin", email="admin@example.com", password="password"
    )


def _next_gway_number_request(params: dict | None = None):
    return RequestFactory().post(
        "/nodes/register/next-gway-number/",
        params or {},
        HTTP_AUTHORIZATION=f"Bearer {GWAY_RESERVATION_TOKEN}",
    )


def _build_request(factory, payload):
    request = factory.post(
        "/nodes/register/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    return request


def _signed_registration_fields(token="signed-token"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signature = private_key.sign(
        token.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "public_key": public_key.decode(),
        "token": token,
        "signature": base64.b64encode(signature).decode(),
    }


@pytest.mark.django_db
def test_register_node_rejects_invalid_enrollment_token_without_creating_node(
    admin_user,
):
    payload = {
        "hostname": "mesh-invalid-token",
        "mac_address": "aa:bb:cc:dd:ee:45",
        "address": "198.51.100.45",
        "port": 8888,
        "public_key": "ssh-rsa AAAAB3Nza-invalid",
        "enrollment_token": "invalid-token",
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 400
    assert not Node.objects.filter(mac_address=payload["mac_address"]).exists()


@pytest.mark.django_db
def test_register_node_does_not_match_public_endpoint_for_untrusted_signature():
    victim = Node.objects.create(
        hostname="victim-node",
        mac_address="aa:bb:cc:dd:ee:60",
        address="198.51.100.60",
        port=8888,
        public_endpoint="victim-node",
        public_key="victim-public-key",
    )
    payload = {
        "hostname": "attacker-node",
        "mac_address": "aa:bb:cc:dd:ee:61",
        "address": "198.51.100.61",
        "port": 8888,
        "public_endpoint": victim.public_endpoint,
        **_signed_registration_fields(),
    }

    request = _build_request(RequestFactory(), payload)
    request.user = AnonymousUser()

    response = register_node(request)

    victim.refresh_from_db()
    body = json.loads(response.content.decode())
    assert response.status_code == 200
    assert body["uuid"] != str(victim.uuid)
    assert victim.hostname == "victim-node"
    assert victim.mac_address == "aa:bb:cc:dd:ee:60"
    assert victim.address == "198.51.100.60"
    assert victim.public_key == "victim-public-key"


@pytest.mark.django_db
def test_register_node_allows_admin_public_endpoint_match(admin_user):
    node = Node.objects.create(
        hostname="admin-endpoint",
        mac_address="aa:bb:cc:dd:ee:62",
        address="198.51.100.62",
        port=8888,
        public_endpoint="admin-endpoint",
    )
    payload = {
        "hostname": "admin-endpoint-updated",
        "mac_address": "aa:bb:cc:dd:ee:63",
        "address": "198.51.100.63",
        "port": 8888,
        "public_endpoint": node.public_endpoint,
    }

    request = _build_request(RequestFactory(), payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    node.refresh_from_db()
    body = json.loads(response.content.decode())
    assert response.status_code == 200
    assert body["uuid"] == str(node.uuid)
    assert node.hostname == "admin-endpoint-updated"
    assert node.mac_address == "aa:bb:cc:dd:ee:63"
    assert node.address == "198.51.100.63"


def test_register_node_accepts_valid_enrollment_token_for_existing_node(admin_user):
    node = Node.objects.create(
        hostname="mesh-existing-token",
        mac_address="aa:bb:cc:dd:ee:46",
        address="198.51.100.46",
        port=8888,
        public_endpoint="mesh-existing-token",
    )
    _, token = issue_enrollment_token(node=node)
    payload = {
        "hostname": node.hostname,
        "mac_address": node.mac_address,
        "address": node.address,
        "port": node.port,
        "public_key": "ssh-rsa AAAAB3Nza-valid",
        "enrollment_token": token,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    node.refresh_from_db()
    assert response.status_code == 200
    assert node.public_key == payload["public_key"]


@pytest.mark.django_db
def test_register_node_allows_enrollment_token_public_endpoint_match():
    node = Node.objects.create(
        hostname="mesh-endpoint-token",
        mac_address="aa:bb:cc:dd:ee:47",
        address="198.51.100.47",
        port=8888,
        public_endpoint="mesh-endpoint-token",
    )
    _, token = issue_enrollment_token(node=node)
    signed_fields = _signed_registration_fields()
    payload = {
        "hostname": node.hostname,
        "mac_address": "aa:bb:cc:dd:ee:48",
        "address": "198.51.100.48",
        "port": node.port,
        "public_endpoint": node.public_endpoint,
        "enrollment_token": token,
        **signed_fields,
    }

    request = _build_request(RequestFactory(), payload)
    request.user = AnonymousUser()

    response = register_node(request)

    node.refresh_from_db()
    body = json.loads(response.content.decode())
    assert response.status_code == 200
    assert body["uuid"] == str(node.uuid)
    assert node.mac_address == payload["mac_address"]
    assert node.public_key == signed_fields["public_key"]


@pytest.mark.django_db
def test_register_node_updates_mesh_identity_fields(admin_user):
    node = Node.objects.create(
        hostname="mesh-existing",
        mac_address="aa:bb:cc:dd:ef:01",
        address="198.51.100.41",
        port=8888,
        public_endpoint="mesh-existing",
    )
    payload = {
        "hostname": node.hostname,
        "mac_address": node.mac_address,
        "address": node.address,
        "port": node.port,
        "mesh_enrollment_state": Node.MeshEnrollmentState.ENROLLED,
        "mesh_key_fingerprint_metadata": {
            "algorithm": "sha256",
            "fingerprint": "abc123",
        },
        "last_mesh_heartbeat": "2026-03-31T12:34:56Z",
        "mesh_capability_flags": ["routing", "store-and-forward"],
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node.refresh_from_db()
    assert node.mesh_enrollment_state == Node.MeshEnrollmentState.ENROLLED
    assert (
        node.mesh_key_fingerprint_metadata == payload["mesh_key_fingerprint_metadata"]
    )
    assert node.last_mesh_heartbeat is not None
    assert node.mesh_capability_flags == sorted(payload["mesh_capability_flags"])


@pytest.mark.django_db
def test_register_node_does_not_claim_reserved_placeholder_by_hostname(admin_user):
    """First contact must not claim reserved placeholder rows by hostname alone."""

    reserved = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
    )
    payload = {
        "hostname": "gway-004",
        "mac_address": "aa:bb:cc:dd:ee:04",
        "address": "10.42.0.4",
        "ipv4_address": "10.42.0.4",
        "port": 8888,
        "trusted": True,
        "current_relation": "Peer",
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["id"] != reserved.id
    reserved.refresh_from_db()
    assert reserved.reserved is True
    assert reserved.mac_address == ""
    assert Node.objects.count() == 2


@pytest.mark.django_db
def test_register_node_does_not_claim_reserved_placeholder_by_address_with_different_hostname(
    admin_user,
):
    reserved = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
    )
    payload = {
        "hostname": "gway-099",
        "mac_address": "aa:bb:cc:dd:ee:99",
        "address": "10.42.0.4",
        "ipv4_address": "10.42.0.4",
        "port": 8888,
        "current_relation": "Peer",
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["id"] != reserved.id
    reserved.refresh_from_db()
    assert reserved.reserved is True
    assert reserved.mac_address == ""
    assert Node.objects.count() == 2


@pytest.mark.django_db
def test_register_node_does_not_claim_reserved_placeholder_without_claim_token():
    reserved = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
        mesh_key_fingerprint_metadata={
            handlers.RESERVATION_CLAIM_TOKEN_HASH_KEY: handlers._hash_reservation_claim_token(
                "claim-token"
            )
        },
    )
    payload = {
        "hostname": "gway-004",
        "mac_address": "aa:bb:cc:dd:ee:04",
        "address": "10.42.0.4",
        "ipv4_address": "10.42.0.4",
        "port": 8888,
        "current_relation": "Peer",
        **_signed_registration_fields(),
    }

    request = _build_request(RequestFactory(), payload)
    request.user = AnonymousUser()

    response = register_node(request)

    assert response.status_code == 409
    body = json.loads(response.content.decode())
    assert body["detail"] == "Reserved node claim token did not match."
    reserved.refresh_from_db()
    assert reserved.reserved is True
    assert reserved.mac_address == ""
    assert Node.objects.count() == 1


@pytest.mark.django_db
def test_register_node_claims_reserved_placeholder_with_claim_token():
    claim_token = "claim-token"
    reserved = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
        mesh_key_fingerprint_metadata={
            handlers.RESERVATION_CLAIM_TOKEN_HASH_KEY: handlers._hash_reservation_claim_token(
                claim_token
            )
        },
    )
    payload = {
        "hostname": "gway-004",
        "mac_address": "aa:bb:cc:dd:ee:04",
        "address": "10.42.0.4",
        "ipv4_address": "10.42.0.4",
        "port": 8888,
        "current_relation": "Peer",
        "reservation_claim_token": claim_token,
        **_signed_registration_fields(),
    }

    request = _build_request(RequestFactory(), payload)
    request.user = AnonymousUser()

    response = register_node(request)

    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["id"] == reserved.id
    reserved.refresh_from_db()
    assert reserved.reserved is False
    assert reserved.mac_address == payload["mac_address"]
    assert handlers.RESERVATION_CLAIM_TOKEN_HASH_KEY not in (
        reserved.mesh_key_fingerprint_metadata or {}
    )
    assert Node.objects.count() == 1


@pytest.mark.django_db
def test_register_node_distinguishes_self_nodes_by_host_instance_id(admin_user):
    shared_mac = "aa:bb:cc:dd:ee:10"
    primary = Node.objects.create(
        hostname="arthexis.com",
        public_endpoint="arthexis",
        mac_address=shared_mac,
        host_instance_id="machine-1",
        current_relation=Node.Relation.SELF,
    )
    Node.objects.create(
        hostname="audi.gelectriic.com",
        public_endpoint="audi-gelectriic-com",
        mac_address=shared_mac,
        host_instance_id="machine-2",
        current_relation=Node.Relation.SELF,
    )

    payload = {
        "hostname": "arthexis.com",
        "public_endpoint": "arthexis",
        "mac_address": shared_mac,
        "host_instance_id": "machine-1",
        "current_relation": "Self",
        "address": "203.0.113.10",
        "port": 8888,
    }
    request = _build_request(RequestFactory(), payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    primary.refresh_from_db()
    assert primary.address == "203.0.113.10"
    assert Node.objects.filter(mac_address=shared_mac).count() == 2


@pytest.mark.django_db
def test_next_gway_number_endpoint_requires_authentication():
    request = RequestFactory().post("/nodes/register/next-gway-number/")

    response = next_gway_number(request)

    assert response.status_code == 401
    assert json.loads(response.content.decode())["detail"] == "authentication required"
    assert Node.objects.count() == 0


@pytest.mark.django_db
def test_next_gway_number_endpoint_rejects_staff_session_without_token(admin_user):
    request = RequestFactory().post("/nodes/register/next-gway-number/")
    request.user = admin_user

    response = next_gway_number(request)

    assert response.status_code == 401
    assert json.loads(response.content.decode())["detail"] == "authentication required"
    assert Node.objects.count() == 0


@pytest.mark.django_db
def test_gway_reservation_lock_reuses_existing_lock_row():
    first_lock_id = handlers._ensure_reservation_lock_node("gway")
    second_lock_id = handlers._ensure_reservation_lock_node("gway")
    lock = Node.objects.get(id=first_lock_id)

    assert second_lock_id == first_lock_id
    assert lock.reserved is False
    assert (
        Node.objects.filter(
            public_endpoint=handlers._reservation_lock_endpoint("gway")
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_gway_reservation_lock_clears_legacy_reserved_lock_row():
    lock = Node.objects.create(
        hostname="gway-reservation-lock",
        public_endpoint=handlers._reservation_lock_endpoint("gway"),
        reserved=True,
    )

    lock_id = handlers._ensure_reservation_lock_node("gway")

    lock.refresh_from_db()
    assert lock_id == lock.id
    assert lock.reserved is False


@pytest.mark.django_db
def test_next_gway_number_endpoint_uses_existing_gway_hostnames(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_GWAY_RESERVATION_TOKEN", GWAY_RESERVATION_TOKEN)
    Node.objects.create(hostname="gway-001")
    Node.objects.create(hostname="gway-005")
    Node.objects.create(hostname="terminal-099")

    request = _next_gway_number_request()

    response = next_gway_number(request)

    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["prefix"] == "gway"
    assert body["next_number"] == 6
    assert body["hostname"] == "gway-006"
    assert body["reserved"] is True
    reserved = Node.objects.get(id=body["node_id"])
    assert reserved.hostname == "gway-006"
    assert reserved.reserved is True
    assert body["claim_token"]
    claim_request = _build_request(
        RequestFactory(),
        {
            "hostname": "gway-006",
            "reservation_claim_token": body["claim_token"],
        },
    )
    claim_payload = handlers.parse_registration_request(claim_request).payload
    assert handlers._reservation_claim_token_matches(
        reserved,
        claim_payload,
    )

    second_response = next_gway_number(request)
    second_body = json.loads(second_response.content.decode())
    assert second_body["next_number"] == 7
    assert second_body["hostname"] == "gway-007"
    assert (
        Node.objects.filter(
            public_endpoint=handlers._reservation_lock_endpoint("gway")
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_next_gway_number_endpoint_respects_minimum_number(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_GWAY_RESERVATION_TOKEN", GWAY_RESERVATION_TOKEN)
    Node.objects.create(hostname="gway-005")

    request = _next_gway_number_request({"prefix": "gway", "minimum_number": "12"})

    response = next_gway_number(request)

    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["next_number"] == 12
    assert body["hostname"] == "gway-012"
    assert Node.objects.get(id=body["node_id"]).reserved is True


@pytest.mark.django_db
def test_next_gway_number_endpoint_bounds_custom_prefix_for_reservation_lock(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_GWAY_RESERVATION_TOKEN", GWAY_RESERVATION_TOKEN)
    raw_prefix = "GWAY " + ("Alpha-" * 20)
    expected_prefix = handlers._clean_gway_number_prefix(raw_prefix)

    request = _next_gway_number_request({"prefix": raw_prefix})

    response = next_gway_number(request)

    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["prefix"] == expected_prefix
    assert len(body["prefix"]) <= handlers.GWAY_PREFIX_MAX_LENGTH
    assert body["hostname"] == f"{expected_prefix}-001"
    assert len(body["hostname"]) <= Node._meta.get_field("hostname").max_length

    lock_endpoint = handlers._reservation_lock_endpoint(expected_prefix)
    assert len(lock_endpoint) <= Node._meta.get_field("public_endpoint").max_length
    assert Node.objects.filter(public_endpoint=lock_endpoint, reserved=False).exists()


@pytest.mark.django_db
def test_find_reserved_node_uses_address_fallback_only_without_hostname():
    claim_token = "claim-token"
    reserved = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
        mesh_key_fingerprint_metadata={
            handlers.RESERVATION_CLAIM_TOKEN_HASH_KEY: handlers._hash_reservation_claim_token(
                claim_token
            )
        },
    )
    request = _build_request(
        RequestFactory(),
        {
            "hostname": "",
            "mac_address": "aa:bb:cc:dd:ee:04",
            "address": "10.42.0.4",
            "ipv4_address": "10.42.0.4",
            "reservation_claim_token": claim_token,
        },
    )
    payload = handlers.parse_registration_request(request).payload

    match = handlers._find_reserved_node_for_payload(
        payload,
        address_value="10.42.0.4",
        ipv4_value="10.42.0.4",
    )

    assert match == reserved


@pytest.mark.django_db
def test_node_info_omits_sensitive_identity_fields(monkeypatch):
    local_mac = "aa:bb:cc:dd:ef:02"
    Node._local_cache.clear()
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: local_mac))
    node = Node.objects.create(
        hostname="mesh-local",
        mac_address=local_mac,
        host_instance_id="machine-1",
        address="198.51.100.42",
        port=8888,
        public_endpoint="mesh-local",
        current_relation=Node.Relation.SELF,
        mesh_enrollment_state=Node.MeshEnrollmentState.PENDING,
        mesh_key_fingerprint_metadata={"algorithm": "sha256"},
        mesh_capability_flags=["routing"],
    )

    request = RequestFactory().get("/nodes/info/")
    response = node_info(request)

    assert response.status_code == 200
    data = json.loads(response.content.decode())
    assert data["mesh_enrollment_state"] == node.mesh_enrollment_state
    assert data["mesh_key_fingerprint_metadata"] == node.mesh_key_fingerprint_metadata
    assert data["mesh_capability_flags"] == node.mesh_capability_flags
    assert "host_instance_id" not in data
    assert "uuid" not in data


@pytest.mark.django_db
def test_node_info_never_exposes_reservation_claim_token(monkeypatch):
    local_mac = "aa:bb:cc:dd:ef:03"
    Node._local_cache.clear()
    monkeypatch.setenv("NODE_RESERVED_CLAIM_TOKEN", "claim-token")
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: local_mac))
    Node.objects.create(
        hostname="gway-004",
        mac_address=local_mac,
        current_relation=Node.Relation.SELF,
    )

    direct_response = node_info(
        RequestFactory().get("/nodes/info/", REMOTE_ADDR="127.0.0.1")
    )
    proxied_response = node_info(
        RequestFactory().get(
            "/nodes/info/",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.10",
        )
    )

    direct_data = json.loads(direct_response.content.decode())
    proxied_data = json.loads(proxied_response.content.decode())
    assert "reservation_claim_token" not in direct_data
    assert "reservation_claim_token" not in proxied_data


@pytest.mark.django_db
def test_sign_token_rejects_traversal_shaped_public_endpoint(tmp_path):
    outside_key = tmp_path / "outside-key"
    outside_key.write_text("not a private key", encoding="utf-8")
    node = Node.objects.create(
        hostname="unsafe-signing-node",
        public_endpoint="unsafe-signing-node",
        base_path=str(tmp_path / "base"),
    )
    node.public_endpoint = "../outside-key"
    data: dict[str, object] = {}

    handlers._sign_token_for_node(data, node, "token")

    assert "token_signature" not in data


@pytest.mark.django_db
def test_get_local_does_not_cache_stale_self_after_mac_conflict(monkeypatch):
    self_node = Node.objects.create(
        hostname="self-node",
        mac_address="00:11:22:33:44:55",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(
        Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff")
    )

    original_filter = Node.objects.filter
    race_inserted = False

    def racing_filter(*args, **kwargs):
        nonlocal race_inserted
        if kwargs == {"mac_address__iexact": "aa:bb:cc:dd:ee:ff"} and not race_inserted:
            race_inserted = True
            Node.objects.create(
                hostname="racer",
                mac_address="aa:bb:cc:dd:ee:ff",
                current_relation=Node.Relation.PEER,
            )
            return Node.objects.none()
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(Node.objects, "filter", racing_filter)

    local = Node.get_local()

    assert local is not None
    assert local.hostname == "racer"
    self_node.refresh_from_db()
    assert self_node.mac_address == "00:11:22:33:44:55"
    assert Node._local_cache["aa:bb:cc:dd:ee:ff"][0].hostname == "racer"


@pytest.mark.django_db
def test_get_local_invalidates_cache_when_timezone_mode_changes(settings, monkeypatch):
    local_mac = "aa:bb:cc:dd:ee:10"
    node = Node.objects.create(
        hostname="timezone-cache",
        mac_address=local_mac,
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    Node._local_cache[local_mac] = (node, timezone.now() + timedelta(minutes=1))
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: local_mac))
    settings.USE_TZ = False

    local = Node.get_local()

    assert local == node
    assert timezone.is_naive(Node._local_cache[local_mac][1])


@pytest.mark.django_db
def test_get_local_does_not_return_deleted_self_after_zero_row_mac_update(monkeypatch):
    self_node = Node.objects.create(
        hostname="deleted-self-node",
        mac_address="00:11:22:33:44:56",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(
        Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:01")
    )

    original_filter = Node.objects.filter
    deleted_during_update = False

    class DeletingUpdate:
        def update(self, **kwargs):
            nonlocal deleted_during_update
            deleted_during_update = True
            original_filter(pk=self_node.pk).delete()
            return 0

    def deleting_filter(*args, **kwargs):
        if kwargs == {"pk": self_node.pk}:
            return DeletingUpdate()
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(Node.objects, "filter", deleting_filter)

    local = Node.get_local()

    assert deleted_during_update is True
    assert local is None
    assert "aa:bb:cc:dd:ee:01" not in Node._local_cache
    assert not original_filter(pk=self_node.pk).exists()


@pytest.mark.django_db
def test_get_local_does_not_rewrite_arbitrary_self_node_when_multiple_exist(
    monkeypatch,
):
    primary = Node.objects.create(
        hostname="self-one",
        mac_address="00:11:22:33:44:61",
        host_instance_id="machine-1",
        current_relation=Node.Relation.SELF,
    )
    secondary = Node.objects.create(
        hostname="self-two",
        mac_address="00:11:22:33:44:62",
        host_instance_id="machine-2",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(
        Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:03")
    )
    monkeypatch.setattr(Node, "get_host_instance_id", classmethod(lambda cls: ""))

    local = Node.get_local()

    assert local is None
    primary.refresh_from_db()
    secondary.refresh_from_db()
    assert primary.mac_address == "00:11:22:33:44:61"
    assert secondary.mac_address == "00:11:22:33:44:62"
    assert "aa:bb:cc:dd:ee:03" not in Node._local_cache


@pytest.mark.django_db
def test_get_local_returns_self_after_transient_mac_update_error(monkeypatch):
    self_node = Node.objects.create(
        hostname="transient-self-node",
        mac_address="00:11:22:33:44:57",
        current_relation=Node.Relation.SELF,
    )
    other_node = Node.objects.create(
        hostname="other-node",
        mac_address="00:11:22:33:44:58",
        current_relation=Node.Relation.PEER,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(
        Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:02")
    )

    original_filter = Node.objects.filter

    class FailingUpdate:
        def update(self, **kwargs):
            raise DatabaseError("simulated transient write failure")

    def failing_filter(*args, **kwargs):
        if kwargs == {"pk": self_node.pk}:
            return FailingUpdate()
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(Node.objects, "filter", failing_filter)

    local = Node.get_local()

    assert local is not None
    assert local.pk == self_node.pk
    assert local.pk != other_node.pk
    self_node.refresh_from_db()
    assert self_node.mac_address == "00:11:22:33:44:57"
    assert "aa:bb:cc:dd:ee:02" not in Node._local_cache


@pytest.mark.django_db
def test_get_local_logs_redacted_mac_values(monkeypatch, caplog):
    self_node = Node.objects.create(
        hostname="self-node",
        mac_address="00:11:22:33:44:55",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(
        Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff")
    )

    original_filter = Node.objects.filter

    class ConflictingUpdate:
        def update(self, **kwargs):
            raise IntegrityError("simulated uniqueness conflict")

    def conflicting_filter(*args, **kwargs):
        if kwargs == {"pk": self_node.pk}:
            return ConflictingUpdate()
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(Node.objects, "filter", conflicting_filter)

    caplog.set_level(logging.WARNING, logger="apps.nodes.models.node")
    Node.get_local()

    conflict_records = [
        rec
        for rec in caplog.records
        if "could not update due to MAC uniqueness conflict" in rec.getMessage()
    ]
    assert conflict_records
    record = conflict_records[-1]
    assert getattr(record, "runtime_mac_redacted", "").startswith("***REDACTED***-")
    assert getattr(record, "stored_mac_redacted", "").startswith("***REDACTED***-")
    assert not hasattr(record, "runtime_mac")
    assert not hasattr(record, "stored_mac")
    assert "aa:bb:cc:dd:ee:ff" not in caplog.text
    assert "00:11:22:33:44:55" not in caplog.text


def _stub_local_registration(monkeypatch, *, hostname: str, ipv4: str, mac: str):
    monkeypatch.setattr(registration, "_resolve_local_role_name", lambda: "Terminal")
    monkeypatch.setattr(registration.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(registration.socket, "getfqdn", lambda _host: hostname)
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda _host: ipv4)
    monkeypatch.setattr(
        Node,
        "_resolve_ip_addresses",
        staticmethod(lambda *_hosts: ([ipv4], [])),
    )
    monkeypatch.setattr(
        Node,
        "_detect_managed_site",
        classmethod(lambda cls: (None, "", False)),
    )
    monkeypatch.setattr(Node, "get_current_mac", classmethod(lambda cls: mac))
    monkeypatch.setattr(
        Node,
        "get_host_instance_id",
        classmethod(lambda cls: "machine-1"),
    )
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "refresh_features", lambda self: None)


@pytest.mark.django_db
def test_register_current_assigns_default_role_upgrade_policy_on_create(monkeypatch):
    policy = UpgradePolicy.objects.create(
        name="Terminal Stable",
        channel=UpgradePolicy.Channel.STABLE,
        interval_minutes=10080,
    )
    NodeRole.objects.create(name="Terminal", default_upgrade_policy=policy)
    _stub_local_registration(
        monkeypatch,
        hostname="terminal-create",
        ipv4="192.0.2.10",
        mac="aa:bb:cc:dd:ee:99",
    )

    node, created = registration.register_current(Node, notify_peers=False)

    assert created is True
    assert node.role.name == "Terminal"
    assert list(node.upgrade_policies.values_list("name", flat=True)) == [policy.name]


@pytest.mark.django_db
def test_register_current_backfills_missing_default_role_upgrade_policy(monkeypatch):
    policy = UpgradePolicy.objects.create(
        name="Terminal Stable",
        channel=UpgradePolicy.Channel.STABLE,
        interval_minutes=10080,
    )
    role = NodeRole.objects.create(name="Terminal", default_upgrade_policy=policy)
    node = Node.objects.create(
        hostname="terminal-refresh",
        mac_address="aa:bb:cc:dd:ee:98",
        address="192.0.2.11",
        port=8888,
        public_endpoint="terminal-refresh",
        role=role,
        current_relation=Node.Relation.SELF,
    )
    node.upgrade_policies.clear()
    _stub_local_registration(
        monkeypatch,
        hostname="terminal-refresh",
        ipv4="192.0.2.11",
        mac="aa:bb:cc:dd:ee:98",
    )

    refreshed, created = registration.register_current(Node, notify_peers=False)

    assert created is False
    assert refreshed.pk == node.pk
    assert list(refreshed.upgrade_policies.values_list("name", flat=True)) == [
        policy.name
    ]
