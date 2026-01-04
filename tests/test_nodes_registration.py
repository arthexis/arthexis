import json
import logging
from uuid import uuid4
from unittest.mock import Mock

import requests

import pytest
from django.urls import reverse

from apps.nodes.models import Node
from django.contrib.sites.models import Site


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
def test_node_changelist_excludes_register_local_tool(admin_client):
    response = admin_client.get(reverse("admin:nodes_node_changelist"))

    assert response.status_code == 200
    assert "Register local host" not in response.content.decode()


@pytest.mark.django_db
def test_register_visitor_view_uses_clean_visitor_base(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    call_count: dict[str, int] = {"count": 0}

    def fake_register_current(cls):
        call_count["count"] += 1
        return node, False

    monkeypatch.setattr(Node, "register_current", classmethod(fake_register_current))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        {"visitor": "visitor.example.com:9999/extra/path"},
    )

    assert response.status_code == 200
    assert call_count["count"] == 1

    context = response.context[-1]
    assert context["token"]
    assert context["info_url"] == reverse("node-info")
    assert context["register_url"] == reverse("register-node")
    assert context["telemetry_url"] == reverse("register-telemetry")
    assert context["visitor_proxy_url"] == reverse("register-visitor-proxy")
    assert context["visitor_info_url"] == "https://127.0.0.1:443/nodes/info/"
    assert (
        context["visitor_register_url"]
        == "https://127.0.0.1:443/nodes/register/"
    )
    assert context["visitor_host"] == "127.0.0.1"
    assert context["visitor_port"] == 443


@pytest.mark.django_db
def test_register_visitor_view_ignores_client_address_headers(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: (node, False)))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        REMOTE_ADDR="198.51.100.5",
        HTTP_X_FORWARDED_FOR="203.0.113.1, 203.0.113.2",
    )

    assert response.status_code == 200
    context = response.context[-1]
    assert context["visitor_error"] is None
    assert context["visitor_info_url"] == "https://127.0.0.1:443/nodes/info/"
    assert context["visitor_register_url"] == "https://127.0.0.1:443/nodes/register/"
    assert context["telemetry_url"] == reverse("register-telemetry")
    assert context["visitor_proxy_url"] == reverse("register-visitor-proxy")


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

        def get(self, url, timeout=None):
            self.requests.append(("get", url))
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

        def post(self, url, json=None, timeout=None):
            self.requests.append(("post", url, json))
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

        def get(self, url, timeout=None):
            self.requests.append(("get", url))
            if url.startswith("https://visitor.test:8888"):
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

        def post(self, url, json=None, timeout=None):
            self.requests.append(("post", url, json))
            if url.startswith("https://visitor.test:8888"):
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
    assert session.requests[0][1].startswith("https://visitor.test:8888")
    assert session.requests[1][1].startswith("https://visitor.test:8000")
    assert session.requests[2][1].startswith("https://visitor.test:8888")
    assert session.requests[3][1].startswith("https://visitor.test:8000")


@pytest.mark.django_db
def test_register_visitor_view_defaults_loopback_port(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: (node, False)))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == 200
    context = response.context[-1]
    assert context["visitor_error"] is None
    assert context["visitor_info_url"] == "https://127.0.0.1:443/nodes/info/"
    assert context["visitor_register_url"] == "https://127.0.0.1:443/nodes/register/"
    assert context["telemetry_url"] == reverse("register-telemetry")


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
