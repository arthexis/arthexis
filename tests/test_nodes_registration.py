import pytest

import json
import logging
import socket
from uuid import uuid4
from unittest.mock import Mock

import requests
import requests_mock

from django.urls import reverse

from apps.nodes.models import Node
from apps.nodes.views import registration as registration_views
from django.contrib.sites.models import Site

pytestmark = pytest.mark.critical

def assert_request_meta(request, expected_url, expected_host_header):
    """Assert URL and Host header for a mocked request."""
    assert request.url == expected_url
    assert request.headers["Host"] == expected_host_header


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

@pytest.mark.django_db
def test_register_visitor_proxy_success(admin_client, monkeypatch):
    """Exercise visitor proxy registration over HTTPS without Session patching."""
    node = Node.objects.create(
        hostname="local",
        address="198.51.100.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
        public_key="local-key",
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    adapter = requests_mock.Adapter()
    monkeypatch.setattr(registration_views, "_HostNameSSLAdapter", lambda *args, **kwargs: adapter)

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "visitor.test":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
            ]
        raise OSError("unknown host")

    monkeypatch.setattr(registration_views.socket, "getaddrinfo", fake_getaddrinfo)

    visitor_info_url = "https://93.184.216.34/nodes/info/"
    visitor_register_url = "https://93.184.216.34/nodes/register/"

    adapter.register_uri(
        "GET",
        visitor_info_url,
        json={
            "hostname": "visitor-host",
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "address": "203.0.113.10",
            "port": 8000,
            "public_key": "visitor-key",
            "features": [],
        },
    )
    adapter.register_uri("POST", visitor_register_url, json={"id": 2, "detail": "ok"})

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

    assert len(adapter.request_history) == 2
    assert_request_meta(
        adapter.request_history[0],
        visitor_info_url,
        "visitor.test",
    )
    assert_request_meta(
        adapter.request_history[1],
        visitor_register_url,
        "visitor.test",
    )

@pytest.mark.django_db
def test_register_visitor_proxy_fallbacks_to_8000(admin_client, monkeypatch):
    """Verify fallback to port 8000 when 8888 is unreachable."""
    node = Node.objects.create(
        hostname="local",
        address="198.51.100.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
        public_key="local-key",
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    adapter = requests_mock.Adapter()
    monkeypatch.setattr(registration_views, "_HostNameSSLAdapter", lambda *args, **kwargs: adapter)

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "visitor.test":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
            ]
        raise OSError("unknown host")

    monkeypatch.setattr(registration_views.socket, "getaddrinfo", fake_getaddrinfo)

    info_url_primary = "https://93.184.216.34:8888/nodes/info/"
    info_url_fallback = "https://93.184.216.34:8000/nodes/info/"
    register_url_primary = "https://93.184.216.34:8888/nodes/register/"
    register_url_fallback = "https://93.184.216.34:8000/nodes/register/"

    adapter.register_uri("GET", info_url_primary, exc=requests.ConnectTimeout)
    adapter.register_uri(
        "GET",
        info_url_fallback,
        json={
            "hostname": "visitor-host",
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "address": "203.0.113.10",
            "port": 8000,
            "public_key": "visitor-key",
            "features": [],
        },
    )
    adapter.register_uri("POST", register_url_primary, exc=requests.ConnectTimeout)
    adapter.register_uri("POST", register_url_fallback, json={"id": 3, "detail": "ok"})

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
    assert len(adapter.request_history) == 4
    assert_request_meta(
        adapter.request_history[0],
        info_url_primary,
        "visitor.test:8888",
    )
    assert_request_meta(
        adapter.request_history[1],
        info_url_fallback,
        "visitor.test:8000",
    )
    assert_request_meta(
        adapter.request_history[2],
        register_url_primary,
        "visitor.test:8888",
    )
    assert_request_meta(
        adapter.request_history[3],
        register_url_fallback,
        "visitor.test:8000",
    )

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
