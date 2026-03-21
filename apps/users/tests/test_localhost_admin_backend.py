import ipaddress

import pytest
from django.test import RequestFactory

from apps.users.backends import LocalhostAdminBackend


def test_get_remote_ip_strips_forwarded_ipv4_port() -> None:
    backend = LocalhostAdminBackend()
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="10.42.0.9:56312")

    remote = backend._get_remote_ip(request)

    assert remote == ipaddress.ip_address("10.42.0.9")


def test_get_remote_ip_strips_bracketed_ipv6_port() -> None:
    backend = LocalhostAdminBackend()
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="[::1]:49877")

    remote = backend._get_remote_ip(request)

    assert remote == ipaddress.ip_address("::1")
