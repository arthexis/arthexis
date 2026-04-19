import hashlib
import json
import logging
from types import SimpleNamespace

import pytest
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole
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

    original_save = Node.save

    def conflicting_save(self, *args, **kwargs):
        if self.pk == self_node.pk and kwargs.get("update_fields") == ["mac_address"]:
            Node.objects.create(
                hostname="racer",
                mac_address="aa:bb:cc:dd:ee:ff",
                current_relation=Node.Relation.PEER,
            )
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Node, "save", conflicting_save)

    local = Node.get_local()

    assert local is not None
    assert local.hostname == "racer"
    self_node.refresh_from_db()
    assert self_node.mac_address == "00:11:22:33:44:55"
    assert Node._local_cache["aa:bb:cc:dd:ee:ff"][0].hostname == "racer"

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

    def raise_conflict(*args, **kwargs):
        raise IntegrityError("simulated uniqueness conflict")

    monkeypatch.setattr(Node, "save", raise_conflict)

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
