import pytest

import json
import logging
import socket
from uuid import uuid4
from unittest.mock import Mock

import requests

from django.urls import reverse

from apps.nodes.models import Node
from apps.nodes.views import registration as registration_views
from django.contrib.sites.models import Site

pytestmark = pytest.mark.critical

@pytest.mark.django_db
def test_node_info_registers_missing_local(client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    register_spy = Mock(return_value=(node, True))

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: None))
    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: register_spy()))

    response = client.get(reverse("node-info"))

    register_spy.assert_called_once_with()
    assert response.status_code == 200
    payload = response.json()
    assert payload["mac_address"] == node.mac_address
    assert payload["network_hostname"] == node.network_hostname
    assert payload["features"] == []

@pytest.mark.django_db
def test_node_info_uses_site_domain_port(monkeypatch, client):
    domain = f"{uuid4().hex}.example.com"
    site = Site.objects.create(domain=domain, name="Example", require_https=False)
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
        base_site=site,
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    response = client.get(reverse("node-info"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["port"] == 443

@pytest.mark.django_db
def test_resolve_visitor_base_defaults_to_loopback():
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from apps.nodes.admin.node_admin import NodeAdmin

    admin_site = AdminSite()
    node_admin = NodeAdmin(Node, admin_site)
    request = RequestFactory().get("/")

    visitor_base, visitor_host, visitor_port, visitor_scheme = node_admin._resolve_visitor_base(
        request
    )

    assert visitor_base == "https://127.0.0.1:443"
    assert visitor_host == "127.0.0.1"
    assert visitor_port == 443
    assert visitor_scheme == "https"

def test_register_visitor_proxy_success(admin_client, monkeypatch):
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
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
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
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
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

@pytest.mark.django_db
def test_register_visitor_telemetry_logs(client, caplog):
    url = reverse("register-telemetry")
    payload = {
        "stage": "integration-test",
        "message": "failed to fetch",
        "target": "http://example.com/nodes/info/",
        "token": "abc123",
        "extra": {"networkIssue": True},
    }

    with caplog.at_level(logging.INFO, logger="register_visitor_node"):
        response = client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_USER_AGENT="pytest-agent/1.0",
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "telemetry stage=integration-test" in caplog.text

@pytest.mark.django_db
def test_register_visitor_telemetry_adds_route_ip(client, caplog, monkeypatch):
    url = reverse("register-telemetry")
    payload = {
        "stage": "integration-test",
        "message": "failed to fetch",
        "target": "https://example.com/nodes/info/",
        "token": "abc123",
    }

    monkeypatch.setattr(
        "apps.nodes.views._get_route_address", lambda host, port: "10.0.0.5"
    )

    with caplog.at_level(logging.INFO, logger="register_visitor_node"):
        response = client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_USER_AGENT="pytest-agent/1.0",
        )

    assert response.status_code == 200
    assert "host_ip=10.0.0.5" in caplog.text
    assert '"target_host": "example.com"' in caplog.text
