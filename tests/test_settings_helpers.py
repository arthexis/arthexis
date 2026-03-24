"""Tests for host validation helpers used by security settings."""

from config.settings_helpers import normalize_forwarded_host_header


def test_normalize_forwarded_host_header_uses_first_proxy_value():
    assert normalize_forwarded_host_header("10.42.0.1, proxy.example") == "10.42.0.1"
