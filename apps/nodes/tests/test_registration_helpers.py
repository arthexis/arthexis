"""Regression coverage for registration auth/network/policy/sanitization helpers."""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from apps.nodes.views.registration import (
    _get_host_port,
    _is_allowed_visitor_url,
    _iter_port_fallback_urls,
    _redact_mac,
    _redact_url_token,
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


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"HTTP_X_FORWARDED_PORT": "8443"}, 8443),
        ({"HTTP_HOST": "edge.example.com:9443"}, 9443),
        ({"HTTP_X_FORWARDED_PROTO": "https"}, 443),
        ({"HTTP_X_FORWARDED_PROTO": "http"}, 80),
    ],
)
def test_get_host_port_infers_from_proxy_headers(headers, expected):
    """Port inference should honor forwarding and host metadata precedence."""

    request = RequestFactory().get("/nodes/info/", **headers)
    assert _get_host_port(request) == expected


def test_iter_port_fallback_urls_generates_legacy_alternative_port():
    """Registration URLs on 8888 should include legacy 8000 fallback."""

    base = "https://visitor.example.com:8888/nodes/info/?token=a"
    assert list(_iter_port_fallback_urls(base)) == [
        base,
        "https://visitor.example.com:8000/nodes/info/?token=a",
    ]


@override_settings(VISITOR_ALLOWED_HOST_SUFFIXES=("example.com", "nodes.internal"))
def test_allowed_visitor_url_matches_suffix_allow_list():
    """Allow-list checks should permit listed suffixes and reject others."""

    assert _is_allowed_visitor_url("https://visitor.example.com/nodes/info/")
    assert _is_allowed_visitor_url("https://alpha.nodes.internal/nodes/info/")
    assert not _is_allowed_visitor_url("https://example.net/nodes/info/")
    assert not _is_allowed_visitor_url("http://visitor.example.com/nodes/info/")


@pytest.mark.parametrize(
    "token,expected",
    [
        ("https://x.example/path?token=abc&x=1", "https://x.example/path?token=%2A%2A%2AREDACTED%2A%2A%2A&x=1"),
        ("https://x.example/path?x=1", "https://x.example/path?x=1"),
    ],
)
def test_redact_url_token_masks_query_parameter(token, expected):
    """Token query string values must be redacted while preserving URL shape."""

    assert _redact_url_token(token) == expected


def test_redact_mac_is_deterministic_and_non_plaintext():
    """MAC redaction should hide source value and remain stable across formatting."""

    first = _redact_mac("AA-BB-CC-DD-EE-FF")
    second = _redact_mac("aabbccddeeff")

    assert first == second
    assert first.startswith("***REDACTED***-")
    assert "AA-BB" not in first


@override_settings(VISITOR_CORS_ALLOWED_ORIGINS=("https://trusted.example",))
def test_add_cors_headers_reflects_only_allowed_origin():
    """CORS credentials should only be enabled for allow-listed origins."""

    from django.http import JsonResponse

    from apps.nodes.views.registration.cors import add_cors_headers

    trusted_request = RequestFactory().options(
        "/nodes/register/",
        HTTP_ORIGIN="https://trusted.example",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="X-Test-Header",
    )
    trusted_response = add_cors_headers(trusted_request, JsonResponse({"detail": "ok"}))
    assert trusted_response["Access-Control-Allow-Origin"] == "https://trusted.example"
    assert trusted_response["Access-Control-Allow-Credentials"] == "true"

    untrusted_request = RequestFactory().options(
        "/nodes/register/",
        HTTP_ORIGIN="https://evil.example",
    )
    untrusted_response = add_cors_headers(untrusted_request, JsonResponse({"detail": "ok"}))
    assert untrusted_response["Access-Control-Allow-Origin"] == "*"
    assert untrusted_response["Access-Control-Allow-Credentials"] == "false"


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


def test_payload_helpers_handle_falsy_strings_and_invalid_utf8():
    """Payload coercion should map falsy strings and tolerate invalid UTF-8."""

    from apps.nodes.views.registration.payload import _coerce_bool, _extract_request_data

    assert _coerce_bool("false") is False
    assert _coerce_bool("  off  ") is False
    assert _coerce_bool("1") is True

    request = RequestFactory().post(
        "/nodes/register/",
        data=bytes([0x80]),
        content_type="application/json",
    )
    data = _extract_request_data(request)
    assert hasattr(data, "get")
