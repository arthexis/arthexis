import pytest

from apps.dns.models import DNSProxyConfig
from apps.nmcli.models import NetworkConnection


@pytest.mark.django_db
def test_dns_proxy_config_combines_nmcli_upstreams():
    connection = NetworkConnection.objects.create(
        connection_id="wifi-1",
        ip4_dns="8.8.8.8 1.1.1.1",
        ip6_dns="2606:4700:4700::1111",
    )
    config = DNSProxyConfig.objects.create(
        name="proxy-one",
        upstream_servers=["9.9.9.9"],
        nmcli_connection=connection,
        include_nmcli_dns=True,
    )

    assert config.get_upstream_servers() == [
        "9.9.9.9",
        "8.8.8.8",
        "1.1.1.1",
        "2606:4700:4700::1111",
    ]


@pytest.mark.django_db
def test_dns_proxy_config_normalizes_listen_host_for_nmcli():
    config = DNSProxyConfig.objects.create(
        name="proxy-two",
        listen_host="0.0.0.0",
    )

    assert config.get_nmcli_dns_entries() == ["127.0.0.1"]
