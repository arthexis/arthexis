import uuid
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from nodes.models import Node


class NodeInfoViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_node_info_uses_reachable_address(self):
        mac = Node.get_current_mac()
        slug = f"test-node-{uuid.uuid4().hex}"
        defaults = {
            "hostname": "test-host",
            "address": "127.0.0.1",
            "port": 8123,
            "public_endpoint": slug,
        }
        node, _ = Node.objects.update_or_create(mac_address=mac, defaults=defaults)

        with patch("nodes.views._get_route_address", return_value="10.42.0.1") as route_mock:
            response = self.client.get(reverse("node-info"), REMOTE_ADDR="10.42.0.2")

        payload = response.json()
        self.assertEqual(payload["address"], "10.42.0.1")
        route_mock.assert_called_once_with("10.42.0.2", node.port)
