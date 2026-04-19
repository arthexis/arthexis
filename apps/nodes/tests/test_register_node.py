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
def test_node_info_handles_missing_private_key_file(monkeypatch, tmp_path, caplog):
    node = Node.objects.create(
        hostname="missing-key",
        mac_address="01:23:45:67:89:ac",
        public_endpoint="missing-key",
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr(
        Node, "register_current", classmethod(lambda cls: (node, False))
    )
    monkeypatch.setattr(Node, "get_base_path", lambda self: tmp_path)

    caplog.set_level(logging.WARNING, logger="register_visitor")
    response = node_info(RequestFactory().get("/nodes/info/", {"token": "abc"}))

    assert response.status_code == 200
    payload = json.loads(response.content.decode())
    assert "token_signature" not in payload
    assert any(
        getattr(record, "attempt", "") == "key_read" for record in caplog.records
    )
    assert any(
        getattr(record, "exception_class", "") == "FileNotFoundError"
        for record in caplog.records
    )


@pytest.mark.django_db
def test_node_info_handles_invalid_private_key_material(monkeypatch, tmp_path, caplog):
    node = Node.objects.create(
        hostname="invalid-key",
        mac_address="01:23:45:67:89:ad",
        public_endpoint="invalid-key",
    )
    security_dir = tmp_path / "security"
    security_dir.mkdir(parents=True, exist_ok=True)
    (security_dir / node.public_endpoint).write_bytes(b"not-a-pem-key")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr(
        Node, "register_current", classmethod(lambda cls: (node, False))
    )
    monkeypatch.setattr(Node, "get_base_path", lambda self: tmp_path)

    caplog.set_level(logging.WARNING, logger="register_visitor")
    response = node_info(RequestFactory().get("/nodes/info/", {"token": "abc"}))

    assert response.status_code == 200
    payload = json.loads(response.content.decode())
    assert "token_signature" not in payload
    assert any(
        getattr(record, "attempt", "") == "key_parse" for record in caplog.records
    )
    assert any(
        getattr(record, "exception_class", "") == "ValueError"
        for record in caplog.records
    )


@pytest.mark.django_db
def test_register_visitor_proxy_uses_safe_success_details(monkeypatch, admin_user):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
                "token": "secret-token",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse(
            {
                "hostname": "host-node",
                "address": "198.51.100.5",
                "port": 8888,
                "mac_address": "00:11:22:33:44:55",
                "base_site_requires_https": True,
            }
        ),
    )
    monkeypatch.setattr(
        handlers,
        "register_node",
        lambda _request: JsonResponse(
            {"id": 9, "detail": "Traceback: internal stack"}, status=200
        ),
    )

    def fake_proxy_request(*, method, **_kwargs):
        if method == "get":
            return (
                {
                    "hostname": "visitor-node",
                    "address": "198.51.100.6",
                    "port": 8888,
                    "mac_address": "00:11:22:33:44:66",
                    "base_site_requires_https": False,
                },
                "https://visitor.example/nodes/info/",
                None,
                1,
            )
        return (
            {"id": 11, "detail": "Exception: visitor stack trace"},
            "https://visitor.example/nodes/register/",
            None,
            2,
        )

    monkeypatch.setattr(handlers, "_try_proxy_json_request", fake_proxy_request)

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 200
    data = json.loads(response.content.decode())
    assert data["host"]["id"] == 9
    assert data["visitor"]["id"] == 11
    assert data["host"]["detail"] == "host registration accepted"
    assert data["visitor"]["detail"] == "visitor confirmation accepted"
    assert data["host_requires_https"] is True
    assert data["visitor_requires_https"] is False
    assert "Traceback" not in data["host"]["detail"]
    assert "Exception" not in data["visitor"]["detail"]


@pytest.mark.django_db
def test_register_visitor_proxy_uses_safe_failure_detail(monkeypatch, admin_user):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse({"hostname": "host-node"}),
    )
    monkeypatch.setattr(
        handlers,
        "register_node",
        lambda _request: JsonResponse(
            {"detail": "Traceback: should stay private"}, status=400
        ),
    )
    monkeypatch.setattr(
        handlers,
        "_try_proxy_json_request",
        lambda *, method, **_kwargs: (
            (
                {
                    "hostname": "visitor-node",
                    "address": "198.51.100.6",
                    "port": 8888,
                    "mac_address": "00:11:22:33:44:66",
                },
                "https://visitor.example/nodes/info/",
                None,
                1,
            )
            if method == "get"
            else ({"id": 11}, "https://visitor.example/nodes/register/", None, 2)
        ),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 400
    data = json.loads(response.content.decode())
    assert data["detail"] == "host registration failed"


@pytest.mark.django_db
def test_register_visitor_proxy_returns_400_when_host_id_missing(
    monkeypatch, admin_user
):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse({"hostname": "host-node"}),
    )
    monkeypatch.setattr(
        handlers,
        "register_node",
        lambda _request: JsonResponse({"detail": "missing id"}, status=200),
    )
    monkeypatch.setattr(
        handlers,
        "_try_proxy_json_request",
        lambda *, method, **_kwargs: (
            (
                {
                    "hostname": "visitor-node",
                    "address": "198.51.100.6",
                    "port": 8888,
                    "mac_address": "00:11:22:33:44:66",
                },
                "https://visitor.example/nodes/info/",
                None,
                1,
            )
            if method == "get"
            else ({"id": 11}, "https://visitor.example/nodes/register/", None, 2)
        ),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 400
    data = json.loads(response.content.decode())
    assert data["detail"] == "host registration failed"


@pytest.mark.django_db
def test_register_visitor_proxy_returns_502_when_host_info_is_not_json(
    monkeypatch, admin_user
):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: HttpResponse("not-json", content_type="text/plain"),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 502
    data = json.loads(response.content.decode())
    assert data["detail"] == "host info unavailable"


@pytest.mark.django_db
@pytest.mark.parametrize("host_status", [200, 201, 204])
def test_register_visitor_proxy_coerces_2xx_to_400_when_host_id_missing(
    monkeypatch, admin_user, host_status
):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse({"hostname": "host-node"}),
    )
    monkeypatch.setattr(
        handlers,
        "register_node",
        lambda _request: JsonResponse({"detail": "missing id"}, status=host_status),
    )
    monkeypatch.setattr(
        handlers,
        "_try_proxy_json_request",
        lambda *, method, **_kwargs: (
            (
                {
                    "hostname": "visitor-node",
                    "address": "198.51.100.6",
                    "port": 8888,
                    "mac_address": "00:11:22:33:44:66",
                },
                "https://visitor.example/nodes/info/",
                None,
                1,
            )
            if method == "get"
            else ({"id": 11}, "https://visitor.example/nodes/register/", None, 2)
        ),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 400
    data = json.loads(response.content.decode())
    assert data["detail"] == "host registration failed"


@pytest.mark.django_db
def test_register_visitor_proxy_returns_502_when_host_register_body_is_not_json(
    monkeypatch, admin_user
):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse({"hostname": "host-node"}),
    )
    monkeypatch.setattr(
        handlers,
        "register_node",
        lambda _request: HttpResponse("not-json", content_type="text/plain"),
    )
    monkeypatch.setattr(
        handlers,
        "_try_proxy_json_request",
        lambda *, method, **_kwargs: (
            (
                {
                    "hostname": "visitor-node",
                    "address": "198.51.100.6",
                    "port": 8888,
                    "mac_address": "00:11:22:33:44:66",
                },
                "https://visitor.example/nodes/info/",
                None,
                1,
            )
            if method == "get"
            else ({"id": 11}, "https://visitor.example/nodes/register/", None, 2)
        ),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 502
    data = json.loads(response.content.decode())
    assert data["detail"] == "host registration failed"


@pytest.mark.django_db
def test_register_visitor_proxy_marks_visitor_confirmation_failed_without_id(
    monkeypatch, admin_user
):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse(
            {
                "hostname": "host-node",
                "address": "198.51.100.5",
                "port": 8888,
                "mac_address": "00:11:22:33:44:55",
                "base_site_requires_https": True,
            }
        ),
    )
    monkeypatch.setattr(
        handlers,
        "register_node",
        lambda _request: JsonResponse({"id": 9, "detail": "ok"}, status=200),
    )
    monkeypatch.setattr(
        handlers,
        "_try_proxy_json_request",
        lambda *, method, **_kwargs: (
            (
                {
                    "hostname": "visitor-node",
                    "address": "198.51.100.6",
                    "port": 8888,
                    "mac_address": "00:11:22:33:44:66",
                    "base_site_requires_https": False,
                },
                "https://visitor.example/nodes/info/",
                None,
                1,
            )
            if method == "get"
            else (
                {"detail": "upstream accepted without id"},
                "https://visitor.example/nodes/register/",
                None,
                2,
            )
        ),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 200
    data = json.loads(response.content.decode())
    assert data["host"]["id"] == 9
    assert data["visitor"]["id"] is None
    assert data["visitor"]["detail"] == "visitor confirmation failed"


@pytest.mark.django_db
def test_register_visitor_proxy_masks_unexpected_proxy_exception(
    monkeypatch, admin_user
):
    factory = RequestFactory()
    request = factory.post(
        "/nodes/register-visitor-proxy/",
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.example/nodes/info/",
                "visitor_register_url": "https://visitor.example/nodes/register/",
            }
        ),
        content_type="application/json",
    )
    request.user = admin_user
    request._cached_user = admin_user

    target = SimpleNamespace(
        url="https://visitor.example/nodes/info/",
        server_hostname="visitor.example",
        host_header="visitor.example",
    )
    monkeypatch.setattr(handlers, "is_allowed_visitor_url", lambda _: True)
    monkeypatch.setattr(handlers, "get_public_targets", lambda _: [target])
    monkeypatch.setattr(
        handlers,
        "node_info",
        lambda _request: JsonResponse({"hostname": "host-node"}),
    )
    monkeypatch.setattr(
        handlers,
        "_try_proxy_json_request",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("internal traceback")),
    )

    response = handlers.register_visitor_proxy(request)

    assert response.status_code == 502
    data = json.loads(response.content.decode())
    assert data["detail"] == "visitor info unavailable"
    assert "traceback" not in data["detail"].lower()


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
