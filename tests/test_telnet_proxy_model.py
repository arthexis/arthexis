from unittest.mock import patch

import pytest

from core.models import TelnetProxy


pytestmark = pytest.mark.django_db


def _create_proxy(**overrides):
    data = {
        "endpoint_host": "127.0.0.1",
        "endpoint_port": 2400,
        "telnet_host": "upstream.local",
        "telnet_port": 23,
    }
    data.update(overrides)
    return TelnetProxy.objects.create(**data)


def test_telnet_proxy_stops_before_instance_delete():
    proxy = _create_proxy(endpoint_port=2420)

    with patch.object(TelnetProxy, "stop", autospec=True) as mock_stop:
        proxy.delete()

    mock_stop.assert_called_once_with(proxy)


def test_telnet_proxy_stops_when_queryset_deletes_instance():
    proxy = _create_proxy(endpoint_port=2421)

    with patch.object(TelnetProxy, "stop", autospec=True) as mock_stop:
        TelnetProxy.objects.filter(pk=proxy.pk).delete()

    mock_stop.assert_called_once()
    deleted_instance = mock_stop.call_args.args[0]
    assert deleted_instance.endpoint_port == proxy.endpoint_port
