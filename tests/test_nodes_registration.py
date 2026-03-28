"""Regression coverage for node registration and visitor proxy flows."""

import json
import socket
from uuid import uuid4

import pytest
import requests
from django.contrib.sites.models import Site
from django.http import JsonResponse
from django.test import RequestFactory
from django.urls import reverse

from apps.nodes.admin.visitor_registration import (
    VisitorRegistrationRequest,
    VisitorRegistrationService,
)
from apps.nodes.models import Node
from apps.nodes.views import registration as registration_views

@pytest.mark.django_db
def test_node_info_registers_missing_local(client, monkeypatch):
    """Ensure node info triggers registration when no local node exists."""
    expected_mac = "00:11:22:33:44:55"
    Node._local_cache.clear()

    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: expected_mac))
    monkeypatch.setattr(
        Node, "_resolve_ip_addresses", classmethod(lambda _, *__: ([], []))
    )
    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(socket, "getfqdn", lambda *_: "test-host.local")
    monkeypatch.setattr(socket, "gethostbyname", lambda *_: "127.0.0.1")

    response = client.get(
        reverse("node-info"),
        HTTP_X_FORWARDED_PROTO="http",
        HTTP_X_FORWARDED_PORT="80",
    )

    assert response.status_code == 200
    created_node = Node.objects.get(mac_address=expected_mac)
    payload = response.json()
    assert payload["mac_address"] == created_node.mac_address
    assert payload["hostname"] == created_node.hostname
    assert payload["network_hostname"] == created_node.network_hostname
    assert payload["address"] == created_node.address
    host_domain = registration_views._get_host_domain(response.wsgi_request)
    advertised_port = created_node.port or created_node.get_preferred_port()
    base_domain = created_node.get_base_domain()
    if base_domain:
        advertised_port = created_node._preferred_site_port(True)
    if host_domain and not base_domain:
        host_port = registration_views._get_host_port(response.wsgi_request)
        preferred_port = created_node.get_preferred_port()
        if host_port in {preferred_port, created_node.port, 80, 443}:
            advertised_port = host_port
        else:
            advertised_port = preferred_port
    assert payload["port"] == advertised_port
    assert set(payload["features"]) == set(
        created_node.features.values_list("slug", flat=True)
    )

@pytest.mark.django_db
def test_visitor_registration_request_post_requires_submitted_host():
    """POST parser should reject requests that omit the submitted visitor host."""
    request = RequestFactory().post(
        "/admin/nodes/node/register-visitor/?visitor=query.example:9443",
        data={"visitor_host": "", "visitor_port": ""},
    )

    parsed = VisitorRegistrationRequest.from_http_request(request, default_port=8888)

    assert parsed.visitor_error == "Visitor address missing. Reload with ?visitor=host[:port]."
    assert parsed.visitor_base is None

def test_visitor_registration_service_success_with_warnings(monkeypatch):
    """Service should expose HTTPS warnings when proxy signals weak transport settings."""

    def fake_proxy(_request):
        return JsonResponse(
            {
                "host": {"id": 1, "detail": "host-ok"},
                "visitor": {"id": 2, "detail": "visitor-ok"},
                "host_requires_https": False,
                "visitor_requires_https": False,
            }
        )

    monkeypatch.setattr("apps.nodes.admin.visitor_registration.register_visitor_proxy", fake_proxy)
    parsed = VisitorRegistrationRequest(
        token="abc123",
        visitor_base="https://visitor.test:443",
        visitor_error=None,
        visitor_host="visitor.test",
        visitor_info_url="https://visitor.test:443/nodes/info/",
        visitor_port=443,
        visitor_register_url="https://visitor.test:443/nodes/register/",
        visitor_scheme="https",
    )

    result = VisitorRegistrationService(user=None).register(parsed)

    assert result.status == "success"
    assert result.host["status"] == "success"
    assert result.visitor["status"] == "success"
    assert "Host node is not configured to require HTTPS. Update its Sites settings." in result.warnings
    assert "Visitor node is not configured to require HTTPS. Update its Sites settings." in result.warnings

def test_visitor_registration_service_missing_visitor_address_short_circuits():
    """Service should not proxy when visitor address parsing already failed."""
    parsed = VisitorRegistrationRequest(
        token="abc123",
        visitor_base=None,
        visitor_error="Visitor address missing. Reload with ?visitor=host[:port].",
        visitor_host="",
        visitor_info_url="",
        visitor_port=None,
        visitor_register_url="",
        visitor_scheme="https",
    )

    result = VisitorRegistrationService(user=None).register(parsed)

    assert result.status == "error"
    assert result.errors == ["Visitor address missing. Reload with ?visitor=host[:port]."]

@pytest.mark.django_db
def test_register_visitor_proxy_success(admin_client, monkeypatch):
    """Visitor registration should succeed when both info and register endpoints respond."""
    node = Node.objects.create(
        hostname="local",
        address="198.51.100.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
        public_key="local-key",
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "visitor.test":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", port or 443),
                )
            ]
        raise OSError("unknown host")

    monkeypatch.setattr(registration_views.socket, "getaddrinfo", fake_getaddrinfo)

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError()

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.requests = []

        def mount(self, prefix, adapter):
            return None

        def get(self, url, timeout=None, headers=None):
            self.requests.append(("get", url, headers))
            return FakeResponse(
                {
                    "hostname": "visitor-host",
                    "mac_address": "aa:bb:cc:dd:ee:ff",
                    "address": "203.0.113.10",
                    "port": 8000,
                    "public_key": "visitor-key",
                    "features": [],
                }
            )

        def post(self, url, json=None, timeout=None, headers=None):
            self.requests.append(("post", url, json, headers))
            return FakeResponse({"id": 2, "detail": "ok"})

    monkeypatch.setattr(requests, "Session", lambda: FakeSession())

    response = admin_client.post(
        reverse("register-visitor-proxy"),
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.test/nodes/info/",
                "visitor_register_url": "https://visitor.test/nodes/register/",
                "token": "",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["host"]["id"]
    assert body["visitor"]["id"] == 2

@pytest.mark.django_db
def test_register_visitor_proxy_fallbacks_to_8000(admin_client, monkeypatch):
    """Registration should retry with port 8000 after :8888 connect timeouts."""
    node = Node.objects.create(
        hostname="local",
        address="198.51.100.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
        public_key="local-key",
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "visitor.test":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", port or 443),
                )
            ]
        raise OSError("unknown host")

    monkeypatch.setattr(registration_views.socket, "getaddrinfo", fake_getaddrinfo)

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError()

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.requests = []

        def mount(self, prefix, adapter):
            return None

        def get(self, url, timeout=None, headers=None):
            self.requests.append(("get", url, headers))
            if url.startswith("https://93.184.216.34:8888"):
                raise requests.ConnectTimeout()
            return FakeResponse(
                {
                    "hostname": "visitor-host",
                    "mac_address": "aa:bb:cc:dd:ee:ff",
                    "address": "203.0.113.10",
                    "port": 8000,
                    "public_key": "visitor-key",
                    "features": [],
                }
            )

        def post(self, url, json=None, timeout=None, headers=None):
            self.requests.append(("post", url, json, headers))
            if url.startswith("https://93.184.216.34:8888"):
                raise requests.ConnectTimeout()
            return FakeResponse({"id": 3, "detail": "ok"})

    sessions: list[FakeSession] = []

    def fake_session_factory():
        session = FakeSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(requests, "Session", fake_session_factory)

    response = admin_client.post(
        reverse("register-visitor-proxy"),
        data=json.dumps(
            {
                "visitor_info_url": "https://visitor.test:8888/nodes/info/",
                "visitor_register_url": "https://visitor.test:8888/nodes/register/",
                "token": "",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert sessions
    session = sessions[-1]
    assert session.requests[0][1].startswith("https://93.184.216.34:8888")
    assert session.requests[1][1].startswith("https://93.184.216.34:8000")
    assert session.requests[2][1].startswith("https://93.184.216.34:8888")
    assert session.requests[3][1].startswith("https://93.184.216.34:8000")
