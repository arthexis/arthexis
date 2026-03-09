"""Tests for trusted proxy client IP resolution helpers."""

from django.test import override_settings

from apps.ocpp.consumers.base.identity import _parse_ip, _resolve_client_ip


def _scope(client_ip, headers=None):
    """Build a minimal ASGI scope for client IP resolution tests."""

    return {
        "client": (client_ip, 12345),
        "headers": headers or [],
    }


def test_resolve_client_ip_keeps_untrusted_direct_client_with_forged_headers():
    """Untrusted direct clients must not be overridden by forwarding headers."""

    scope = _scope(
        "203.0.113.10",
        headers=[
            (b"x-forwarded-for", b"198.51.100.1, 10.0.0.20"),
            (b"forwarded", b"for=198.51.100.2"),
            (b"x-real-ip", b"198.51.100.3"),
        ],
    )

    with override_settings(OCPP_TRUSTED_PROXY_IPS=["10.0.0.0/8"]):
        assert _resolve_client_ip(scope) == "203.0.113.10"


@override_settings(OCPP_TRUSTED_PROXY_IPS=["10.0.0.0/8", "192.168.0.0/16"])
def test_resolve_client_ip_x_forwarded_for_selects_rightmost_non_trusted_hop():
    """Trusted proxies should resolve the rightmost non-trusted client hop from XFF."""

    scope = _scope(
        "10.0.0.5",
        headers=[
            (b"x-forwarded-for", b"198.51.100.25, 192.168.1.2, 10.1.2.3"),
        ],
    )

    assert _resolve_client_ip(scope) == "198.51.100.25"


@override_settings(OCPP_TRUSTED_PROXY_IPS=["10.0.0.0/8", "192.168.0.0/16"])
def test_resolve_client_ip_forwarded_header_parsing_variants():
    """Forwarded header for= values should handle quoting and IPv6 bracket/port forms."""

    cases = [
        ('for=198.51.100.40;proto=https', '198.51.100.40'),
        ('for="198.51.100.41:8443";proto=https', '198.51.100.41'),
        ('for="[2001:db8::44]:8443";proto=https', '2001:db8::44'),
        ('for=unknown, for=192.168.1.10, for=198.51.100.42', '198.51.100.42'),
    ]

    for forwarded_value, expected in cases:
        scope = _scope(
            "10.0.0.9",
            headers=[(b"forwarded", forwarded_value.encode("latin1"))],
        )
        assert _resolve_client_ip(scope) == expected


@override_settings(OCPP_TRUSTED_PROXY_IPS=["10.0.0.0/8"])
def test_resolve_client_ip_falls_back_to_connection_for_empty_or_malformed_headers():
    """Malformed or empty forwarding headers should fall back to the connection IP."""

    cases = [
        [],
        [(b"x-forwarded-for", b"unknown, not-an-ip")],
        [(b"forwarded", b"for=unknown,for=bad-host:abc")],
        [(b"x-real-ip", b"not-an-ip")],
    ]

    for headers in cases:
        scope = _scope("10.0.0.12", headers=headers)
        assert _resolve_client_ip(scope) == "10.0.0.12"


def test_parse_ip_handles_unknown_invalid_and_host_inputs():
    """IP parsing should reject unknown and invalid host literals while handling ports."""

    cases = [
        (None, None),
        ("", None),
        ("unknown", None),
        ("UNKNOWN", None),
        ("999.1.1.1", None),
        ("example.com", None),
        ("not-an-ip:443", None),
        ("198.51.100.50", "198.51.100.50"),
        ("198.51.100.50:443", "198.51.100.50"),
        ("[2001:db8::50]:443", "2001:db8::50"),
        ('for="[2001:db8::51]:443"', "2001:db8::51"),
        ("for=unknown", None),
    ]

    for raw, expected in cases:
        parsed = _parse_ip(raw)
        assert (str(parsed) if parsed is not None else None) == expected
