"""Regression coverage for registration auth/network/policy/sanitization helpers."""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from apps.nodes.views.registration import register_node

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
