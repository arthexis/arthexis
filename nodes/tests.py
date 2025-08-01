from pathlib import Path
from unittest.mock import patch
import socket
import threading
import http.server
import socketserver

from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from .models import Node, NodeScreenshot, NginxConfig


class NodeTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_register_and_list_node(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "local",
                "address": "127.0.0.1",
                "port": 8000,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Node.objects.count(), 1)

        list_resp = self.client.get(reverse("node-list"))
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data["nodes"]), 1)
        self.assertEqual(data["nodes"][0]["hostname"], "local")

    @patch("nodes.views.capture_screenshot")
    def test_capture_screenshot(self, mock_capture):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname, address="127.0.0.1", port=80
        )
        mock_capture.return_value = Path("screenshots/test.png")
        response = self.client.get(reverse("node-screenshot"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["screenshot"], "screenshots/test.png")
        self.assertEqual(data["node"], node.id)
        mock_capture.assert_called_once()
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        self.assertEqual(NodeScreenshot.objects.first().node, node)

class NodeAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_register_current_host(self):
        url = reverse("admin:nodes_node_register_current")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Node.objects.count(), 1)


class NginxConfigTests(TestCase):
    def _run_server(self, port):
        handler = http.server.SimpleHTTPRequestHandler
        httpd = socketserver.TCPServer(("", port), handler)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.daemon = True
        thread.start()
        return httpd

    def test_render_config_contains_backup(self):
        cfg = NginxConfig(name='test', server_name='example.com', primary_upstream='remote:8000', backup_upstream='127.0.0.1:8000')
        text = cfg.render_config()
        self.assertIn('backup', text)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;', text)

    def test_connection(self):
        server = self._run_server(8123)
        try:
            cfg = NginxConfig(name='test', server_name='example.com', primary_upstream='127.0.0.1:8123')
            self.assertTrue(cfg.test_connection())
            cfg.primary_upstream = '127.0.0.1:8999'
            self.assertFalse(cfg.test_connection())
        finally:
            server.shutdown()
            server.server_close()

