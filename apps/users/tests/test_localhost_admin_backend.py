import ipaddress

from django.test import RequestFactory, override_settings

from apps.users.backends import LocalhostAdminBackend


def test_get_remote_ip_strips_remote_addr_ipv4_port() -> None:
    backend = LocalhostAdminBackend()
    request = RequestFactory().get("/", REMOTE_ADDR="10.42.0.9:56312")

    remote = backend._get_remote_ip(request)

    assert remote == ipaddress.ip_address("10.42.0.9")


def test_get_remote_ip_strips_remote_addr_bracketed_ipv6_port() -> None:
    backend = LocalhostAdminBackend()
    request = RequestFactory().get("/", REMOTE_ADDR="[::1]:49877")

    remote = backend._get_remote_ip(request)

    assert remote == ipaddress.ip_address("::1")


@override_settings(TRUSTED_PROXIES=("127.0.0.1/32",))
def test_get_remote_ip_rejects_forwarded_ipv4_port_from_trusted_proxy() -> None:
    backend = LocalhostAdminBackend()
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="10.42.0.9:56312",
    )

    remote = backend._get_remote_ip(request)

    assert remote == ipaddress.ip_address("127.0.0.1")


@override_settings(TRUSTED_PROXIES=("127.0.0.1/32",))
def test_get_remote_ip_rejects_forwarded_bracketed_ipv6_port_from_trusted_proxy() -> None:
    backend = LocalhostAdminBackend()
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="[::1]:49877",
    )

    remote = backend._get_remote_ip(request)

    assert remote == ipaddress.ip_address("127.0.0.1")
