import hashlib
import json
import logging
from types import SimpleNamespace

import pytest
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.db import DatabaseError, IntegrityError
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole
from apps.nodes.models.upgrade_policy import UpgradePolicy
from apps.nodes.services import registration
from apps.nodes.services.enrollment import issue_enrollment_token
from apps.nodes.views import node_info, register_node
from apps.nodes.views.registration import handlers
from apps.sites.models import SiteProfile


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin", email="admin@example.com", password="password"
    )


def _build_request(factory, payload):
    request = factory.post(
        "/nodes/register/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    return request


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
def test_find_reserved_node_uses_address_fallback_only_without_hostname():
    reserved = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
    )
    request = _build_request(
        RequestFactory(),
        {
            "hostname": "",
            "mac_address": "aa:bb:cc:dd:ee:04",
            "address": "10.42.0.4",
            "ipv4_address": "10.42.0.4",
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
def test_node_info_omits_sensitive_identity_fields():
    node = Node.objects.create(
        hostname="mesh-local",
        mac_address="aa:bb:cc:dd:ef:02",
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
