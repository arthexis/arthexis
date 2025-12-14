from unittest.mock import Mock

import pytest
from django.urls import reverse

from apps.nodes.models import Node


@pytest.mark.django_db
def test_node_info_registers_missing_local(client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    register_spy = Mock(return_value=(node, True))

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: None))
    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: register_spy()))

    response = client.get(reverse("node-info"))

    register_spy.assert_called_once_with()
    assert response.status_code == 200
    payload = response.json()
    assert payload["mac_address"] == node.mac_address
    assert payload["network_hostname"] == node.network_hostname
    assert payload["features"] == []


@pytest.mark.django_db
def test_node_changelist_excludes_register_local_tool(admin_client):
    response = admin_client.get(reverse("admin:nodes_node_changelist"))

    assert response.status_code == 200
    assert "Register local host" not in response.content.decode()
