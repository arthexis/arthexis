"""Regression coverage for registration auth/network/policy/sanitization helpers."""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from apps.nodes.views.registration import (
    _is_allowed_visitor_url,
    register_node,
)

@pytest.mark.django_db
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
