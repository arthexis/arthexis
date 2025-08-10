from pathlib import Path
from unittest.mock import patch
import socket
import threading
import http.server
import socketserver

from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin
from django_celery_beat.models import PeriodicTask

from .admin import RecipeAdmin

from .models import (
    Node,
    NodeScreenshot,
    NodeMessage,
    NginxConfig,
    Recipe,
    Step,
    Pattern,
    TextSample,
)
from .tasks import capture_node_screenshot, sample_clipboard


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

    def test_public_api_get_and_post(self):
        node = Node.objects.create(
            hostname="public", address="127.0.0.1", port=8001, enable_public_api=True
        )
        url = reverse("node-public-endpoint", args=[node.public_endpoint])

        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["hostname"], "public")

        post_resp = self.client.post(
            url, data="hello", content_type="text/plain"
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertEqual(NodeMessage.objects.count(), 1)
        msg = NodeMessage.objects.first()
        self.assertEqual(msg.body, "hello")
        self.assertEqual(msg.node, node)

    def test_public_api_disabled(self):
        node = Node.objects.create(
            hostname="nopublic", address="127.0.0.2", port=8002
        )
        url = reverse("node-public-endpoint", args=[node.public_endpoint])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_enable_clipboard_polling_creates_task(self):
        node = Node.objects.create(hostname="clip", address="127.0.0.1", port=9000)
        task_name = f"poll_clipboard_node_{node.pk}"
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())
        node.enable_clipboard_polling = True
        node.save()
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        node.enable_clipboard_polling = False
        node.save()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

class NodeAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="nodes-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_register_current_host(self):
        url = reverse("admin:nodes_node_register_current")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Node.objects.count(), 1)

    @patch("nodes.admin.capture_screenshot")
    def test_capture_screenshot_from_admin(self, mock_capture):
        mock_capture.return_value = Path("screenshots/test.png")
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname, address="127.0.0.1", port=80
        )
        url = reverse("admin:nodes_nodescreenshot_capture")
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertContains(
            response, "Screenshot saved to screenshots/test.png"
        )


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


class RecipeTests(TestCase):
    def test_step_sync_and_text_update(self):
        recipe = Recipe.objects.create(name="sample")
        Step.objects.create(recipe=recipe, order=1, script="echo one")
        Step.objects.create(recipe=recipe, order=2, script="echo two")
        recipe.refresh_from_db()
        self.assertEqual(recipe.full_script, "echo one\necho two")

        recipe.full_script = "first\nsecond"

        class DummyForm:
            cleaned_data = {"full_script": recipe.full_script}

        admin_instance = RecipeAdmin(Recipe, admin.site)
        admin_instance.save_model(None, recipe, DummyForm(), False)

        steps = list(recipe.steps.order_by("order").values_list("script", flat=True))
        self.assertEqual(steps, ["first", "second"])


class PatternMatchTests(TestCase):
    def test_match_with_sigil(self):
        pattern = Pattern.objects.create(mask="This is [not] good", priority=1)
        substitutions = pattern.match("Indeed, This is very good.")
        self.assertEqual(substitutions, {"not": "very"})

    def test_match_without_sigil(self):
        pattern = Pattern.objects.create(mask="simple", priority=1)
        substitutions = pattern.match("a simple example")
        self.assertEqual(substitutions, {})

    def test_no_match(self):
        pattern = Pattern.objects.create(mask="missing", priority=1)
        self.assertIsNone(pattern.match("nothing to see"))


class TextSampleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "clipboard_admin", "admin@example.com", "pass"
        )
        self.client.login(username="clipboard_admin", password="pass")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_creates_sample(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:nodes_textsample_from_clipboard")
        response = self.client.get(url, follow=True)
        self.assertEqual(TextSample.objects.count(), 1)
        self.assertEqual(TextSample.objects.first().content, "clip text")
        self.assertFalse(TextSample.objects.first().automated)
        self.assertContains(response, "Text sample added from clipboard")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_skips_duplicate(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:nodes_textsample_from_clipboard")
        self.client.get(url, follow=True)
        resp = self.client.get(url, follow=True)
        self.assertEqual(TextSample.objects.count(), 1)
        self.assertContains(resp, "Duplicate sample not created")


class ClipboardTaskTests(TestCase):
    @patch("nodes.tasks.pyperclip.paste")
    def test_sample_clipboard_task_creates_sample(self, mock_paste):
        mock_paste.return_value = "task text"
        sample_clipboard()
        self.assertEqual(TextSample.objects.count(), 1)
        self.assertEqual(TextSample.objects.first().content, "task text")
        self.assertTrue(TextSample.objects.first().automated)
        # Duplicate should not create another sample
        sample_clipboard()
        self.assertEqual(TextSample.objects.count(), 1)

    @patch("nodes.tasks.capture_screenshot")
    @patch("nodes.tasks.socket.gethostname")
    def test_capture_node_screenshot_task(self, mock_hostname, mock_capture):
        mock_hostname.return_value = "host"
        node = Node.objects.create(hostname="host", address="127.0.0.1", port=8000)
        mock_capture.return_value = Path("screenshots/test.png")
        capture_node_screenshot("http://example.com")
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")

