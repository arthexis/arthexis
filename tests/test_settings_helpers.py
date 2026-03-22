"""Tests for host validation helpers used by security settings."""

from config.settings_helpers import extract_ip_from_host, validate_host_with_subnets


def test_extract_ip_from_host_handles_trailing_dot_and_port():
    ip = extract_ip_from_host("10.42.0.1.:8000")

    assert ip is not None
    assert str(ip) == "10.42.0.1"


def test_validate_host_with_subnets_accepts_trailing_dot_ip_host():
    is_allowed = validate_host_with_subnets(
        "10.42.0.1.",
        ["10.42.0.0/16"],
    )

    assert is_allowed is True


def test_validate_host_with_subnets_uses_first_forwarded_host_value():
    is_allowed = validate_host_with_subnets(
        "10.42.0.1,127.0.0.1",
        ["10.42.0.0/16"],
    )

    assert is_allowed is True
