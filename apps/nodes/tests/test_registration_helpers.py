"""Regression coverage for registration auth/network/policy/sanitization helpers."""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory, override_settings

from apps.nodes.views.registration import (
    _is_allowed_visitor_url,
    register_node,
)

@pytest.mark.django_db
@pytest.mark.critical
def test_register_node_rejects_invalid_signature_without_authenticated_user():
    """Unsigned requests with malformed signature must be rejected."""

    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:77",
        "address": "192.0.2.30",
        "port": 8888,
        "public_key": "invalid-key",
        "token": "signed-token",
        "signature": "bad-signature",
    }
    request = RequestFactory().post(
        "/nodes/register/", data=json.dumps(payload), content_type="application/json"
    )

    response = register_node(request)

    assert response.status_code == 403
    assert json.loads(response.content.decode())["detail"] == "invalid signature"

@override_settings(VISITOR_ALLOWED_HOST_SUFFIXES=("example.com", "nodes.internal"))
def test_allowed_visitor_url_matches_suffix_allow_list():
    """Allow-list checks should permit listed suffixes and reject others."""

    assert _is_allowed_visitor_url("https://visitor.example.com/nodes/info/")
    assert _is_allowed_visitor_url("https://alpha.nodes.internal/nodes/info/")
    assert not _is_allowed_visitor_url("https://example.net/nodes/info/")
    assert not _is_allowed_visitor_url("http://visitor.example.com/nodes/info/")

@override_settings(TRUSTED_PROXIES=("10.0.0.1",))
def test_get_client_ip_uses_forwarded_for_only_for_trusted_proxy():
    """X-Forwarded-For should be honored only when REMOTE_ADDR is trusted."""

    from apps.nodes.views.registration.network import get_client_ip

    trusted_request = RequestFactory().get(
        "/nodes/info/",
        REMOTE_ADDR="10.0.0.1",
        HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1",
    )
    assert get_client_ip(trusted_request) == "203.0.113.9"

    untrusted_request = RequestFactory().get(
        "/nodes/info/",
        REMOTE_ADDR="198.51.100.7",
        HTTP_X_FORWARDED_FOR="203.0.113.10",
    )
    assert get_client_ip(untrusted_request) == "198.51.100.7"


def test_host_helpers_return_empty_for_invalid_host_header(monkeypatch):
    """Host helpers should gracefully handle rejected host headers."""

    from apps.nodes.views.registration.network import _get_host_domain, _get_host_ip

    request = RequestFactory().get("/nodes/info/", HTTP_HOST="bad host")
    def _raise_disallowed_host():
        raise DisallowedHost("bad host")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    assert _get_host_ip(request) == ""
    assert _get_host_domain(request) == ""


def test_append_token_returns_original_value_for_malformed_url_input():
    """Malformed URL inputs should be returned as-is."""

    from apps.nodes.views.registration.network import append_token

    invalid_url = ["not-a-url"]
    assert append_token(invalid_url, "token") == invalid_url


def test_get_host_port_parses_forwarded_port_and_falls_back_to_proto():
    """Forwarded port should win when valid and fall back when malformed."""

    from apps.nodes.views.registration.network import _get_host_port

    request_with_port = RequestFactory().get(
        "/nodes/info/",
        HTTP_HOST="node.example.com",
        HTTP_X_FORWARDED_PORT="8443",
    )
    assert _get_host_port(request_with_port) == 8443

    request_with_invalid_port = RequestFactory().get(
        "/nodes/info/",
        HTTP_HOST="node.example.com",
        HTTP_X_FORWARDED_PORT="8443,443",
        HTTP_X_FORWARDED_PROTO="https",
    )
    assert _get_host_port(request_with_invalid_port) == 443


def test_get_host_port_ignores_disallowed_host_and_uses_proto_fallback(monkeypatch):
    """Port derivation should not trust HTTP_HOST when get_host() is disallowed."""

    from apps.nodes.views.registration.network import _get_host_port

    request = RequestFactory().get(
        "/nodes/info/",
        HTTP_HOST="node.example.com:9999",
        HTTP_X_FORWARDED_PROTO="https",
    )

    def _raise_disallowed_host():
        raise DisallowedHost("bad host")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    assert _get_host_port(request) == 443


def test_iter_port_fallback_urls_handles_malformed_inputs():
    """Fallback URL iterator should tolerate malformed URL values."""

    from apps.nodes.views.registration.network import iter_port_fallback_urls

    invalid_url = ["not-a-url"]
    assert list(iter_port_fallback_urls(invalid_url)) == [invalid_url]

    malformed_port_url = "https://example.com:badport/ocpp/"
    assert list(iter_port_fallback_urls(malformed_port_url)) == [malformed_port_url]
