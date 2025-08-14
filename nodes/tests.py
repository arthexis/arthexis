import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from pathlib import Path
from unittest.mock import patch, call
import socket
import threading
import http.server
import socketserver
import base64
import tempfile
import subprocess

from django.test import Client, TestCase, override_settings, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin
from django_celery_beat.models import PeriodicTask
from django.conf import settings
from django.core.management import call_command

from .admin import RecipeAdmin, NMCLITemplateAdmin

from .models import (
    Node,
    NodeScreenshot,
    NodeMessage,
    NginxConfig,
    NMCLITemplate,
    SystemdUnit,
    Recipe,
    Step,
    TextPattern,
    TextSample,
)
from .tasks import capture_node_screenshot, sample_clipboard


class NodeTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="nodeuser", password="pwd"
        )
        self.client.force_login(self.user)

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
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"test")
        mock_capture.return_value = Path("screenshots/test.png")
        response = self.client.get(reverse("node-screenshot"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["screenshot"], "screenshots/test.png")
        self.assertEqual(data["node"], node.id)
        mock_capture.assert_called_once()
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.method, "GET")

    @patch("nodes.views.capture_screenshot")
    def test_duplicate_screenshot_skipped(self, mock_capture):
        hostname = socket.gethostname()
        Node.objects.create(hostname=hostname, address="127.0.0.1", port=80)
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "dup.png"
        file_path.write_bytes(b"dup")
        mock_capture.return_value = Path("screenshots/dup.png")
        self.client.get(reverse("node-screenshot"))
        self.client.get(reverse("node-screenshot"))
        self.assertEqual(NodeScreenshot.objects.count(), 1)

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

    def test_clipboard_polling_creates_task(self):
        node = Node.objects.create(hostname="clip", address="127.0.0.1", port=9000)
        task_name = f"poll_clipboard_node_{node.pk}"
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())
        node.clipboard_polling = True
        node.save()
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        node.clipboard_polling = False
        node.save()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_screenshot_polling_creates_task(self):
        node = Node.objects.create(hostname="shot", address="127.0.0.1", port=9100)
        task_name = f"capture_screenshot_node_{node.pk}"
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())
        node.screenshot_polling = True
        node.save()
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        node.screenshot_polling = False
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
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"admin")
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
        self.assertEqual(screenshot.method, "ADMIN")
        self.assertContains(
            response, "Screenshot saved to screenshots/test.png"
        )

    def test_view_screenshot_in_change_admin(self):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        with file_path.open("wb") as fh:
            fh.write(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mP8/5+hHgAFgwJ/lSdX6QAAAABJRU5ErkJggg=="
                )
            )
        screenshot = NodeScreenshot.objects.create(path="screenshots/test.png")
        url = reverse("admin:nodes_nodescreenshot_change", args=[screenshot.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data:image/png;base64")


class NMCLITemplateTests(TestCase):
    def test_required_node_creates_periodic_task(self):
        node = Node.objects.create(hostname="nmcli", address="127.0.0.1", port=9700)
        template = NMCLITemplate.objects.create(connection_name="demo")
        template.required_nodes.add(node)
        task_name = f"check_nmcli_node_{node.pk}"
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        template.required_nodes.remove(node)
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())


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


class SystemdUnitTests(TestCase):
    def test_render_and_parse(self):
        unit = SystemdUnit(
            name="arthexis",
            description="arthexis.com",
            documentation="https://arthexis.com",
            user="arthe",
            exec_start="/home/arthe/arthexis/start.sh",
            wanted_by="default.target",
        )
        text = unit.render_unit()
        self.assertIn("Description=arthexis.com", text)
        parsed = SystemdUnit.parse_config("arthexis", text)
        self.assertEqual(parsed.exec_start, "/home/arthe/arthexis/start.sh")


class SystemdUnitInstallCommandTests(TestCase):
    def test_install_writes_file_and_calls_systemctl(self):
        unit = SystemdUnit.objects.create(
            name="demo",
            description="demo service",
            exec_start="/bin/true",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(SYSTEMD_UNIT_ROOT=tmpdir):
                with patch("subprocess.run") as mock_run:
                    call_command("install_systemd_unit", unit.name)
                service_path = Path(tmpdir) / "demo.service"
                self.assertTrue(service_path.exists())
                content = service_path.read_text()
                self.assertIn("ExecStart=/bin/true", content)
                mock_run.assert_has_calls(
                    [
                        call(["systemctl", "daemon-reload"], check=True),
                        call(["systemctl", "enable", unit.name], check=True),
                        call(["systemctl", "restart", unit.name], check=True),
                    ]
                )
                self.assertEqual(mock_run.call_count, 3)


class SystemdUnitStatusTests(TestCase):
    def test_installed_flag(self):
        unit = SystemdUnit(name="demo", description="demo", exec_start="/bin/true")
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(SYSTEMD_UNIT_ROOT=tmpdir):
                self.assertFalse(unit.is_installed())
                path = Path(tmpdir) / "demo.service"
                path.write_text("")
                self.assertTrue(unit.is_installed())

    def test_running_flag(self):
        unit = SystemdUnit(name="demo", description="demo", exec_start="/bin/true")
        with patch("subprocess.run") as mock_run:
            self.assertTrue(unit.is_running())
            mock_run.assert_called_with(
                ["systemctl", "is-active", "demo.service"], check=True
            )
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(3, ["systemctl"]),
        ):
            self.assertFalse(unit.is_running())


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


class TextPatternMatchTests(TestCase):
    def test_match_with_sigil(self):
        pattern = TextPattern.objects.create(mask="This is [not] good", priority=1)
        result = pattern.match("Indeed, This is very good.")
        self.assertEqual(result, "This is very good")

    def test_match_without_sigil(self):
        pattern = TextPattern.objects.create(mask="simple", priority=1)
        result = pattern.match("a simple example")
        self.assertEqual(result, "simple")

    def test_no_match(self):
        pattern = TextPattern.objects.create(mask="missing", priority=1)
        self.assertIsNone(pattern.match("nothing to see"))

    def test_match_multiple_sigils(self):
        pattern = TextPattern.objects.create(
            mask="Hello [first] [last]", priority=1
        )
        result = pattern.match("Well, Hello John Doe!")
        self.assertEqual(result, "Hello John Doe!")


class TextPatternAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "pattern_admin", "admin@example.com", "pass"
        )
        self.client.login(username="pattern_admin", password="pass")

    @patch("pyperclip.paste")
    def test_test_clipboard_action(self, mock_paste):
        mock_paste.return_value = "This is very good"
        pattern = TextPattern.objects.create(mask="This is [not] good", priority=1)
        url = reverse("admin:nodes_textpattern_changelist")
        response = self.client.post(
            url,
            {"action": "test_clipboard", "_selected_action": [pattern.pk]},
            follow=True,
        )
        msgs = [m.message for m in response.context["messages"]]
        self.assertIn("Matched 'This is [not] good' -> 'This is very good'", msgs)


class TextSampleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "clipboard_admin", "admin@example.com", "pass"
        )
        self.client.login(username="clipboard_admin", password="pass")

    @patch("nodes.admin.socket.gethostname")
    @patch("pyperclip.paste")
    def test_add_from_clipboard_creates_sample(self, mock_paste, mock_hostname):
        mock_paste.return_value = "clip text"
        mock_hostname.return_value = "host"
        url = reverse("admin:nodes_textsample_from_clipboard")
        response = self.client.get(url, follow=True)
        self.assertEqual(TextSample.objects.count(), 1)
        sample = TextSample.objects.first()
        self.assertEqual(sample.content, "clip text")
        self.assertFalse(sample.automated)
        self.assertIsNone(sample.node)
        self.assertContains(response, "Text sample added from clipboard")

    @patch("nodes.admin.socket.gethostname")
    @patch("pyperclip.paste")
    def test_add_from_clipboard_skips_duplicate(self, mock_paste, mock_hostname):
        mock_paste.return_value = "clip text"
        mock_hostname.return_value = "host"
        url = reverse("admin:nodes_textsample_from_clipboard")
        self.client.get(url, follow=True)
        resp = self.client.get(url, follow=True)
        self.assertEqual(TextSample.objects.count(), 1)
        self.assertContains(resp, "Duplicate sample not created")


class ClipboardTaskTests(TestCase):
    @patch("nodes.tasks.socket.gethostname")
    @patch("nodes.tasks.pyperclip.paste")
    def test_sample_clipboard_task_creates_sample(self, mock_paste, mock_hostname):
        mock_paste.return_value = "task text"
        mock_hostname.return_value = "host"
        Node.objects.create(hostname="host", address="127.0.0.1", port=8000)
        with patch.dict("os.environ", {"PORT": "8000"}):
            sample_clipboard()
        self.assertEqual(TextSample.objects.count(), 1)
        sample = TextSample.objects.first()
        self.assertEqual(sample.content, "task text")
        self.assertTrue(sample.automated)
        self.assertIsNotNone(sample.node)
        self.assertEqual(sample.node.hostname, "host")
        # Duplicate should not create another sample
        with patch.dict("os.environ", {"PORT": "8000"}):
            sample_clipboard()
        self.assertEqual(TextSample.objects.count(), 1)

    @patch("nodes.tasks.capture_screenshot")
    @patch("nodes.tasks.socket.gethostname")
    def test_capture_node_screenshot_task(self, mock_hostname, mock_capture):
        mock_hostname.return_value = "host"
        node = Node.objects.create(hostname="host", address="127.0.0.1", port=8000)
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"task")
        mock_capture.return_value = Path("screenshots/test.png")
        capture_node_screenshot("http://example.com")
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "TASK")


class NMCLITemplateAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("nmcli", "nm@example.com", "pass")
        self.factory = RequestFactory()
        self.admin = NMCLITemplateAdmin(NMCLITemplate, admin.site)
        self.admin.message_user = lambda *args, **kwargs: None

    @patch("nodes.admin.subprocess.run")
    def test_import_active_populates_fields(self, mock_run):
        def side_effect(args, capture_output, text, check):
            if args == ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"]:
                return subprocess.CompletedProcess(args, 0, stdout="wifi1\n", stderr="")
            field = args[3]
            mapping = {
                "GENERAL.DEVICE": "GENERAL.DEVICE:wlan0\n",
                "GENERAL.AUTOCONNECT-PRIORITY": "GENERAL.AUTOCONNECT-PRIORITY:5\n",
                "GENERAL.AUTOCONNECT": "GENERAL.AUTOCONNECT:yes\n",
                "IP4.ADDRESS[1]": "IP4.ADDRESS[1]:192.168.1.10/24\n",
                "IP4.GATEWAY": "IP4.GATEWAY:192.168.1.1\n",
                "IP4.NEVER_DEFAULT": "IP4.NEVER_DEFAULT:no\n",
                "802-11-WIRELESS-SECURITY.KEY-MGMT": "802-11-WIRELESS-SECURITY.KEY-MGMT:wpa-psk\n",
                "802-11-WIRELESS.SSID": "802-11-WIRELESS.SSID:MyWifi\n",
                "802-11-WIRELESS-SECURITY.PSK": "802-11-WIRELESS-SECURITY.PSK:pass\n",
                "802-11-WIRELESS.BAND": "802-11-WIRELESS.BAND:a\n",
            }
            stdout = mapping.get(field, f"{field}:\n")
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        mock_run.side_effect = side_effect
        request = self.factory.post("/")
        request.user = self.user
        with patch("nodes.admin.os.name", "posix"):
            self.admin.import_active(request, NMCLITemplate.objects.none())

        tpl = NMCLITemplate.objects.get(connection_name="wifi1")
        self.assertEqual(tpl.assigned_device, "wlan0")
        self.assertEqual(tpl.priority, 5)
        self.assertTrue(tpl.autoconnect)
        self.assertEqual(tpl.static_ip, "192.168.1.10")
        self.assertEqual(tpl.static_mask, "24")
        self.assertEqual(tpl.static_gateway, "192.168.1.1")
        self.assertTrue(tpl.allow_outbound)
        self.assertEqual(tpl.security_type, "wpa-psk")
        self.assertEqual(tpl.ssid, "MyWifi")
        self.assertEqual(tpl.password, "pass")
        self.assertEqual(tpl.band, "a")

