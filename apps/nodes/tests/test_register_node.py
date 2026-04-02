import hashlib
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.db import IntegrityError
from django.test import RequestFactory

import pytest

from apps.nodes.models import Node, NodeRole
from apps.nodes.services.enrollment import issue_enrollment_token
from apps.nodes.services import registration
from apps.nodes.views import node_info, register_node


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
def test_register_node_logs_attempt_and_success(admin_user, caplog):
    NodeRole.objects.get_or_create(name="Terminal")
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "address": "192.0.2.10",
        "port": 8888,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    caplog.set_level(logging.INFO, logger="apps.nodes.views")
    response = register_node(request)

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert any("Node registration attempt" in message for message in messages)
    assert any("Node registration succeeded" in message for message in messages)

@pytest.mark.django_db
def test_register_node_logs_validation_failure(admin_user, caplog):
    factory = RequestFactory()
    request = _build_request(
        factory,
        {
            "hostname": "missing-mac",
            "address": "198.51.100.10",
        },
    )
    request.user = admin_user
    request._cached_user = admin_user

    caplog.set_level(logging.INFO, logger="apps.nodes.views")
    response = register_node(request)

    assert response.status_code == 400
    messages = [record.getMessage() for record in caplog.records]
    assert any("Node registration attempt" in message for message in messages)
    assert any("Node registration failed" in message for message in messages)

@pytest.mark.django_db
def test_register_node_sets_cors_headers_without_origin(admin_user):
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:11",
        "address": "192.0.2.20",
        "port": 8888,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "*"
    assert response["Access-Control-Allow-Headers"] == "Content-Type"
    assert response["Access-Control-Allow-Methods"] == "POST, OPTIONS"

@pytest.mark.django_db
def test_register_node_allows_authenticated_user_with_invalid_signature(admin_user):
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:22",
        "address": "192.0.2.30",
        "port": 8888,
        "public_key": "invalid-key",
        "token": "signed-token",
        "signature": "bad-signature",
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.hostname == payload["hostname"]

@pytest.mark.django_db
def test_register_node_links_base_site_when_domain_matches(admin_user):
    site = Site.objects.create(domain="linked.example.com", name="Linked")
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:33",
        "address": "192.0.2.40",
        "port": 8888,
        "base_site_domain": site.domain,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.base_site_id == site.id

@pytest.mark.django_db
def test_register_node_updates_base_site_for_existing_node(admin_user):
    site = Site.objects.create(domain="update.example.com", name="Update")
    node = Node.objects.create(
        hostname="existing",
        mac_address="aa:bb:cc:dd:ee:44",
        address="198.51.100.40",
        port=8888,
        public_endpoint="existing-endpoint",
    )
    payload = {
        "hostname": node.hostname,
        "mac_address": node.mac_address,
        "address": node.address,
        "port": node.port,
        "base_site_domain": site.domain,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node.refresh_from_db()
    assert node.base_site_id == site.id


@pytest.mark.django_db
def test_register_node_rejects_invalid_enrollment_token_without_creating_node(admin_user):
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
        "mesh_key_fingerprint_metadata": {"algorithm": "sha256", "fingerprint": "abc123"},
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
    assert node.mesh_key_fingerprint_metadata == payload["mesh_key_fingerprint_metadata"]
    assert node.last_mesh_heartbeat is not None
    assert node.mesh_capability_flags == sorted(payload["mesh_capability_flags"])


@pytest.mark.django_db
def test_node_info_includes_mesh_identity_fields():
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
    assert data["host_instance_id"] == node.host_instance_id
    assert data["uuid"] == str(node.uuid)

@pytest.mark.django_db
def test_register_current_logs_to_local_logger(settings, caplog):
    settings.LOG_DIR = settings.BASE_DIR / "logs"
    NodeRole.objects.get_or_create(name="Terminal")

    caplog.set_level(logging.INFO, logger="register_local_node")

    node, created = Node.register_current(notify_peers=False)

    assert node is not None
    assert caplog.records
    messages = [record.getMessage() for record in caplog.records]
    assert any("Local node registration started" in message for message in messages)
    assert any(
        "Local node registration created" in message
        or "Local node registration updated" in message
        or "Local node registration refreshed" in message
        for message in messages
    )

@pytest.mark.django_db
def test_register_current_uses_managed_site_domain(settings, caplog):
    caplog.set_level(logging.INFO, logger="register_local_node")
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")

    site = Site.objects.get_current()
    site.domain = "arthexis.com"
    site.name = "Arthexis"
    site.managed = True
    site.require_https = True
    site.save()

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.hostname == "arthexis.com"
    assert node.network_hostname == "arthexis.com"
    assert node.address == "arthexis.com"
    assert node.base_site_id == site.id
    assert node.port == 443


@pytest.mark.django_db
def test_register_current_prefers_node_role_from_env_when_settings_missing(settings, monkeypatch, tmp_path):
    """Registration should use NODE_ROLE env value before lock-file fallback."""
    monkeypatch.setattr(Node, "_resolve_ip_addresses", classmethod(lambda cls, *hosts: ([], [])))
    monkeypatch.setattr(registration.socket, "getfqdn", lambda host: "")
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda host: "127.0.0.1")
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")
    control_role, _ = NodeRole.objects.get_or_create(name="Control")

    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = None
    monkeypatch.setenv("NODE_ROLE", "Control")

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.role_id == control_role.id


@pytest.mark.django_db
def test_register_current_uses_role_lock_when_node_role_is_missing(settings, monkeypatch, tmp_path):
    """Registration should honor legacy lock-file role resolution when NODE_ROLE is unset."""
    monkeypatch.setattr(Node, "_resolve_ip_addresses", classmethod(lambda cls, *hosts: ([], [])))
    monkeypatch.setattr(registration.socket, "getfqdn", lambda host: "")
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda host: "127.0.0.1")
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")
    control_role, _ = NodeRole.objects.get_or_create(name="Control")

    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = None
    monkeypatch.delenv("NODE_ROLE", raising=False)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.role_id == control_role.id


@pytest.mark.django_db
def test_register_current_prefers_settings_node_role_over_lock(settings, monkeypatch, tmp_path):
    """Registration should use settings.NODE_ROLE before legacy lock role."""
    monkeypatch.setattr(Node, "_resolve_ip_addresses", classmethod(lambda cls, *hosts: ([], [])))
    monkeypatch.setattr(registration.socket, "getfqdn", lambda host: "")
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda host: "127.0.0.1")
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")
    control_role, _ = NodeRole.objects.get_or_create(name="Control")

    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = "Control"
    monkeypatch.delenv("NODE_ROLE", raising=False)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "role.lck").write_text("Terminal", encoding="utf-8")

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.role_id == control_role.id


@pytest.mark.django_db
def test_register_current_reloads_lock_when_settings_node_role_is_bootstrap_terminal(settings, monkeypatch, tmp_path):
    """Registration should still honor runtime lock-file updates when settings.NODE_ROLE is bootstrapped Terminal."""
    monkeypatch.setattr(Node, "_resolve_ip_addresses", classmethod(lambda cls, *hosts: ([], [])))
    monkeypatch.setattr(registration.socket, "getfqdn", lambda host: "")
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda host: "127.0.0.1")
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")
    control_role, _ = NodeRole.objects.get_or_create(name="Control")

    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = "Terminal"
    monkeypatch.delenv("NODE_ROLE", raising=False)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.role_id == control_role.id


@pytest.mark.django_db
def test_register_current_normalizes_env_role_name_case(settings, monkeypatch, tmp_path):
    """Registration should normalize lowercase NODE_ROLE values."""
    monkeypatch.setattr(Node, "_resolve_ip_addresses", classmethod(lambda cls, *hosts: ([], [])))
    monkeypatch.setattr(registration.socket, "getfqdn", lambda host: "")
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda host: "127.0.0.1")
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")
    control_role, _ = NodeRole.objects.get_or_create(name="Control")

    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = None
    monkeypatch.setenv("NODE_ROLE", "control")

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.role_id == control_role.id


@pytest.mark.django_db
def test_register_current_defaults_to_terminal_role(settings, monkeypatch, tmp_path):
    """Registration should default to Terminal when no role configuration exists."""
    monkeypatch.setattr(Node, "_resolve_ip_addresses", classmethod(lambda cls, *hosts: ([], [])))
    monkeypatch.setattr(registration.socket, "getfqdn", lambda host: "")
    monkeypatch.setattr(registration.socket, "gethostbyname", lambda host: "127.0.0.1")
    monkeypatch.setattr(Node, "ensure_keys", lambda self: None)
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))
    Node.objects.all().delete()
    Node._local_cache.clear()
    terminal_role, _ = NodeRole.objects.get_or_create(name="Terminal")

    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = None
    monkeypatch.delenv("NODE_ROLE", raising=False)

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.role_id == terminal_role.id

@pytest.mark.django_db
def test_node_info_prefers_base_site_domain(monkeypatch):
    site = Site.objects.create(domain="base.example.test", name="Base Example")
    node = Node.objects.create(
        hostname="original.local",
        mac_address="01:23:45:67:89:ab",
        port=8888,
        public_endpoint="base-example",
        base_site=site,
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    factory = RequestFactory()
    request = factory.get("/nodes/info/")

    response = node_info(request)
    data = json.loads(response.content.decode())

    assert data["hostname"] == "base.example.test"
    assert data["address"] == "base.example.test"
    assert data["contact_hosts"][0] == "base.example.test"
    assert data["base_site_domain"] == site.domain

@pytest.mark.django_db
def test_get_local_refreshes_self_node_mac_on_mismatch(monkeypatch, caplog):
    """Node.get_local should refresh stale SELF node MAC addresses."""
    stale_node = Node.objects.create(
        hostname="self-node",
        mac_address="00:11:22:33:44:55",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))

    caplog.set_level(logging.WARNING, logger="apps.nodes.models.node")
    local = Node.get_local()

    assert local is not None
    assert local.pk == stale_node.pk
    stale_node.refresh_from_db()
    assert stale_node.mac_address == "aa:bb:cc:dd:ee:ff"
    assert any("refreshed stale self-node MAC address" in rec.getMessage() for rec in caplog.records)


@pytest.mark.django_db
def test_get_local_updates_self_node_when_stored_mac_is_empty(monkeypatch):
    self_node = Node.objects.create(
        hostname="self-node",
        mac_address="",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))

    local = Node.get_local()

    assert local is not None
    assert local.pk == self_node.pk
    self_node.refresh_from_db()
    assert self_node.mac_address == "aa:bb:cc:dd:ee:ff"


@pytest.mark.django_db
def test_get_local_does_not_cache_stale_self_after_mac_conflict(monkeypatch):
    self_node = Node.objects.create(
        hostname="self-node",
        mac_address="00:11:22:33:44:55",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))

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
def test_get_local_keeps_self_node_mac_when_runtime_mac_is_in_use(monkeypatch, caplog):
    """Node.get_local should avoid stealing a MAC already assigned to another node."""
    self_node = Node.objects.create(
        hostname="self-node",
        mac_address="00:11:22:33:44:55",
        current_relation=Node.Relation.SELF,
    )
    Node.objects.create(
        hostname="other-node",
        mac_address="aa:bb:cc:dd:ee:ff",
        current_relation=Node.Relation.PEER,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))

    caplog.set_level(logging.WARNING, logger="apps.nodes.models.node")
    local = Node.get_local()

    assert local is not None
    assert local.hostname == "other-node"
    self_node.refresh_from_db()
    assert self_node.mac_address == "00:11:22:33:44:55"


@pytest.mark.django_db
def test_get_local_logs_redacted_mac_values(monkeypatch, caplog):
    """MAC values in Node.get_local warnings must never be logged in clear text."""

    self_node = Node.objects.create(
        hostname="self-node",
        mac_address="00:11:22:33:44:55",
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: "aa:bb:cc:dd:ee:ff"))

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


def test_redact_mac_for_log_masks_plaintext_value():
    """Node MAC log redaction should be deterministic and avoid plain text output."""

    from apps.nodes.models.node import _redact_mac_for_log

    redacted = _redact_mac_for_log("AA-BB-CC-DD-EE-FF")
    expected_hash = hashlib.sha256("aabbccddeeff".encode("utf-8")).hexdigest()[:12]

    assert redacted == f"***REDACTED***-{expected_hash}"
    assert _redact_mac_for_log("aabbccddeeff") == redacted


@pytest.mark.django_db
@pytest.mark.parametrize("relation_value", ["Sibling", "SIBLING"])
def test_register_node_preserves_sibling_relation(admin_user, relation_value):
    payload = {
        "hostname": "visitor-host-sibling",
        "mac_address": "aa:bb:cc:dd:ee:66",
        "address": "192.0.2.66",
        "port": 8888,
        "current_relation": relation_value,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.current_relation == Node.Relation.SIBLING


@pytest.mark.django_db
def test_register_node_demotes_conflicting_self_relation_to_sibling(admin_user):
    Node.objects.create(
        hostname="self-a",
        mac_address="aa:bb:cc:dd:ee:10",
        host_instance_id="machine-10",
        current_relation=Node.Relation.SELF,
    )
    payload = {
        "hostname": "self-b",
        "mac_address": "aa:bb:cc:dd:ee:11",
        "host_instance_id": "machine-10",
        "address": "192.0.2.11",
        "port": 8889,
        "current_relation": "SELF",
    }

    request = _build_request(RequestFactory(), payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.current_relation == Node.Relation.SIBLING


@pytest.mark.django_db
def test_register_node_retries_as_sibling_on_self_host_constraint_conflict(admin_user, monkeypatch):
    payload = {
        "hostname": "self-race",
        "mac_address": "aa:bb:cc:dd:ee:77",
        "host_instance_id": "machine-77",
        "address": "192.0.2.77",
        "port": 8899,
        "current_relation": "SELF",
    }

    request = _build_request(RequestFactory(), payload)
    request.user = admin_user
    request._cached_user = admin_user

    original_get_or_create = Node.objects.get_or_create
    calls = {"count": 0}

    def fake_get_or_create(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise IntegrityError("nodes_node_self_host_instance_unique")
        return original_get_or_create(*args, **kwargs)

    monkeypatch.setattr(Node.objects, "get_or_create", fake_get_or_create)

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.current_relation == Node.Relation.SIBLING
