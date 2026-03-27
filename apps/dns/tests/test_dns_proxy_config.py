"""Tests for DNSProxyConfig upstream resolution behavior."""

from __future__ import annotations

from apps.dns.models import DNSProxyConfig


def test_get_upstream_servers_uses_stored_nmcli_snapshot_without_runtime_fk():
    """Stored nmcli snapshot fields should still contribute upstream servers."""

    config = DNSProxyConfig(
        name="proxy-1",
        upstream_servers=["1.1.1.1, 8.8.8.8", "8.8.8.8"],
        include_nmcli_dns=True,
        nmcli_ip4_dns="9.9.9.9 1.1.1.1",
        nmcli_ip6_dns="2606:4700:4700::1111",
    )

    assert config.get_upstream_servers() == [
        "1.1.1.1",
        "8.8.8.8",
        "9.9.9.9",
        "2606:4700:4700::1111",
    ]


def test_get_upstream_servers_skips_nmcli_snapshot_when_disabled():
    """Stored nmcli snapshot fields should be ignored when include_nmcli_dns is false."""

    config = DNSProxyConfig(
        name="proxy-2",
        upstream_servers=["1.1.1.1"],
        include_nmcli_dns=False,
        nmcli_ip4_dns="9.9.9.9",
        nmcli_ip6_dns="2606:4700:4700::1111",
    )

    assert config.get_upstream_servers() == ["1.1.1.1"]
