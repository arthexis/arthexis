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

    def test_node_info_prefers_request_domain(self):
        mac = Node.get_current_mac()
        slug = f"test-node-{uuid.uuid4().hex}"
        defaults = {
            "hostname": "internal-host",
            "address": "10.0.0.5",
            "port": 8123,
            "public_endpoint": slug,
        }
        Node.objects.update_or_create(mac_address=mac, defaults=defaults)

        with patch("nodes.views._get_route_address", return_value=""):
            response = self.client.get(
                reverse("node-info"), HTTP_HOST="arthexis.com"
            )

        payload = response.json()
        self.assertEqual(payload["hostname"], "arthexis.com")
        self.assertEqual(payload["address"], "arthexis.com")

    def test_node_info_ignores_localhost_domain(self):
        mac = Node.get_current_mac()
        slug = f"test-node-{uuid.uuid4().hex}"
        defaults = {
            "hostname": "gway-002",
            "address": "10.0.0.5",
            "port": 8123,
            "public_endpoint": slug,
        }
        Node.objects.update_or_create(mac_address=mac, defaults=defaults)

        with patch("nodes.views._get_route_address", return_value="192.168.1.2"):
            response = self.client.get(
                reverse("node-info"), HTTP_HOST="localhost:8888"
            )

        payload = response.json()
        self.assertEqual(payload["hostname"], "gway-002")
        self.assertEqual(payload["address"], "192.168.1.2")
