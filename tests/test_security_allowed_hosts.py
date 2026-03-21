"""Regression tests for security host allow-list defaults."""

import pytest
from django.conf import settings
from django.http import request as http_request


def test_allowed_hosts_include_control_plane_ip():
    """Control-plane LAN host should be present for direct URL access."""

    assert "10.42.0.1" in settings.ALLOWED_HOSTS


def test_validate_host_accepts_control_plane_ip_with_port():
    """Host validation should accept requests sent to 10.42.0.1 with a port."""

    assert http_request.validate_host("10.42.0.1:8000", settings.ALLOWED_HOSTS)


def test_allowed_hosts_include_requested_lan_ip():
    """Requested LAN IP should be present for direct URL access."""

    assert "192.168.129.10" in settings.ALLOWED_HOSTS


def test_validate_host_accepts_requested_lan_ip_with_port():
    """Host validation should accept requests sent to 192.168.129.10 with a port."""

    assert http_request.validate_host("192.168.129.10:8000", settings.ALLOWED_HOSTS)
