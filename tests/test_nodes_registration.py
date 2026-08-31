"""Regression coverage for node registration and visitor proxy flows."""

import json
import logging
import socket
from uuid import uuid4

import pytest
import requests
from django.contrib.sites.models import Site
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory
from django.urls import reverse

from apps.nodes.admin.visitor_registration import (
    VisitorRegistrationRequest,
    VisitorRegistrationService,
)
from apps.nodes.models import Node
from apps.nodes.views import registration as registration_views
from apps.nodes.views.registration.payload import build_payload


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError()

    def json(self):
        return self._payload


class _SessionHarness:
    def __init__(self, *, get_behavior, post_behavior=None):
        self.get_behavior = get_behavior
        self.post_behavior = post_behavior
        self.requests = []

    def mount(self, prefix, adapter):
        return None

    def get(self, url, timeout=None, headers=None):
        self.requests.append(("get", url, headers))
        return self.get_behavior(url)

    def post(self, url, json=None, timeout=None, headers=None):
        self.requests.append(("post", url, json, headers))
        if self.post_behavior is None:
            return _FakeResponse({"id": 2, "detail": "ok"})
        return self.post_behavior(url)


def _visitor_node_info_payload(*, mac_address: str, address: str = "203.0.113.10") -> dict[str, object]:
    return {
        "hostname": "visitor-host",
        "mac_address": mac_address,
        "address": address,
        "port": 8000,
        "public_key": "visitor-key",
        "features": [],
    }


def _register_visitor_proxy_request_payload(*, with_8888: bool = False) -> dict[str, str]:
    base = "https://visitor.test:8888" if with_8888 else "https://visitor.test"
    return {
        "visitor_info_url": f"{base}/nodes/info/",
        "visitor_register_url": f"{base}/nodes/register/",
        "token": "",
    }


def _post_register_visitor_proxy(admin_client, *, with_8888: bool = False):
    return admin_client.post(
        reverse("register-visitor-proxy"),
        data=json.dumps(_register_visitor_proxy_request_payload(with_8888=with_8888)),
        content_type="application/json",
    )


def _assert_fallback_requests(session) -> None:
    assert session.requests[0][1].startswith("https://93.184.216.34:8888")
    assert session.requests[1][1].startswith("https://93.184.216.34:8000")
    assert session.requests[2][1].startswith("https://93.184.216.34:8888")
    assert session.requests[3][1].startswith("https://93.184.216.34:8000")


def _assert_proxy_failure(response, *, detail: str) -> None:
    assert response.status_code == 502
    assert response.json()["detail"] == detail


def _assert_proxy_success(response) -> dict[str, object]:
    assert response.status_code == 200
    return response.json()


def _set_payload_builder_runtime_error(monkeypatch, *, message: str = "boom") -> None:
    monkeypatch.setattr(
        "apps.nodes.views.registration.handlers._build_registration_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(message)),
    )


def _patch_requests_session(monkeypatch, factory):
    sessions = []

    def _session_factory():
        session = factory()
        sessions.append(session)
        return session

    monkeypatch.setattr(requests, "Session", _session_factory)
    return sessions


def _session_with_visitor_info(
    *,
    mac_address: str,
    address: str = "203.0.113.10",
    post_behavior=None,
):
    return _SessionHarness(
        get_behavior=lambda _url: _FakeResponse(
            _visitor_node_info_payload(mac_address=mac_address, address=address)
        ),
        post_behavior=post_behavior,
    )


def _session_with_visitor_info_fallback_8000(*, mac_address: str):
    def _get_behavior(url):
        if url.startswith("https://93.184.216.34:8888"):
            raise requests.ConnectTimeout()
        return _FakeResponse(_visitor_node_info_payload(mac_address=mac_address))

    def _post_behavior(url):
        if url.startswith("https://93.184.216.34:8888"):
            raise requests.ConnectTimeout()
        return _FakeResponse({"id": 3, "detail": "ok"})

    return _SessionHarness(
        get_behavior=_get_behavior,
        post_behavior=_post_behavior,
    )


def _configure_visitor_proxy_node(monkeypatch, *, hostname: str, mac_address: str):
    node = Node.objects.create(
        hostname=hostname,
        address="198.51.100.1",
        mac_address=mac_address,
        port=8888,
        public_endpoint=f"{hostname}-endpoint",
        public_key="local-key",
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "visitor.test":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port or 443),
                )
            ]
        raise OSError("unknown host")

    monkeypatch.setattr(registration_views.socket, "getaddrinfo", fake_getaddrinfo)


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

    assert (
        parsed.visitor_error
        == "Visitor address missing. Reload with ?visitor=host[:port]."
    )
    assert parsed.visitor_base is None


def test_visitor_registration_service_handles_non_json_proxy_response(monkeypatch):
    """Service should normalize non-JSON proxy errors into a structured result."""

    def fake_proxy(_request):
        return HttpResponse("not-json", status=502, content_type="text/plain")

    monkeypatch.setattr(
        "apps.nodes.admin.visitor_registration.register_visitor_proxy", fake_proxy
    )
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

    assert result.status == "error"
    assert result.summary == {
        "status": "error",
        "message": "Registration proxy returned an invalid response.",
    }
    assert result.errors == ["Registration proxy returned an invalid response."]


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

    monkeypatch.setattr(
        "apps.nodes.admin.visitor_registration.register_visitor_proxy", fake_proxy
    )
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
    assert (
        "Host node is not configured to require HTTPS. Update its Sites settings."
        in result.warnings
    )
    assert (
        "Visitor node is not configured to require HTTPS. Update its Sites settings."
        in result.warnings
    )


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
    assert result.errors == [
        "Visitor address missing. Reload with ?visitor=host[:port]."
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("with_8888", "session_factory", "assert_session"),
    [
        pytest.param(
            False,
            lambda: _session_with_visitor_info(mac_address="aa:bb:cc:dd:ee:ff"),
            lambda _sessions: None,
            id="direct-success",
        ),
        pytest.param(
            True,
            lambda: _session_with_visitor_info_fallback_8000(
                mac_address="aa:bb:cc:dd:ee:ff"
            ),
            lambda sessions: _assert_fallback_requests(sessions[-1]),
            id="fallback-to-8000",
        ),
    ],
)
def test_register_visitor_proxy_success_paths(
    admin_client, monkeypatch, with_8888, session_factory, assert_session
):
    """Visitor registration should support direct success and :8888 fallback success."""
    _configure_visitor_proxy_node(
        monkeypatch,
        hostname="local",
        mac_address="00:11:22:33:44:55",
    )

    sessions = _patch_requests_session(monkeypatch, session_factory)

    response = _post_register_visitor_proxy(admin_client, with_8888=with_8888)

    body = _assert_proxy_success(response)
    assert body["host"]["id"]
    assert body["visitor"]["id"] in {2, 3}
    assert_session(sessions)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("hostname", "mac_address", "patch_payload_builder", "post_behavior", "expected_detail"),
    [
        pytest.param(
            "local-partial-failure",
            "00:11:22:33:44:66",
            False,
            lambda _url: (_ for _ in ()).throw(
                requests.ConnectTimeout("visitor register timed out")
            ),
            "visitor confirmation failed",
            id="visitor-confirmation-timeout",
        ),
        pytest.param(
            "local-unexpected-failure",
            "00:11:22:33:44:77",
            True,
            None,
            "registration failed",
            id="unexpected-registration-error",
        ),
    ],
)
def test_register_visitor_proxy_failure_paths(
    admin_client,
    monkeypatch,
    hostname,
    mac_address,
    patch_payload_builder,
    post_behavior,
    expected_detail,
):
    """Proxy should report structured failure details for downstream and internal errors."""
    if patch_payload_builder:
        _set_payload_builder_runtime_error(monkeypatch)
    _configure_visitor_proxy_node(
        monkeypatch,
        hostname=hostname,
        mac_address=mac_address,
    )
    _patch_requests_session(
        monkeypatch,
        lambda: _session_with_visitor_info(
            mac_address="aa:bb:cc:dd:ee:aa",
            address="203.0.113.11",
            post_behavior=post_behavior,
        ),
    )

    response = _post_register_visitor_proxy(admin_client)

    _assert_proxy_failure(response, detail=expected_detail)


@pytest.mark.django_db
def test_register_visitor_telemetry_logs(client, caplog):
    """Telemetry endpoint should record structured registration diagnostics."""
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
    """Telemetry logging should include the routed host IP when available."""
    url = reverse("register-telemetry")
    payload = {
        "stage": "integration-test",
        "message": "failed to fetch",
        "target": "https://example.com/nodes/info/",
        "token": "abc123",
    }

    monkeypatch.setattr(
        "apps.nodes.views.registration.handlers._get_route_address",
        lambda host, port: "10.0.0.5",
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
