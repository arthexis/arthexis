import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from pathlib import Path
from unittest.mock import patch, call, MagicMock
import socket
import base64
import json
import uuid
from tempfile import TemporaryDirectory
import shutil
import time

from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin
from django.contrib.sites.models import Site
from django_celery_beat.models import PeriodicTask
from django.conf import settings
from .actions import NodeAction
from selenium.common.exceptions import WebDriverException
from .utils import capture_screenshot

from .models import (
    Node,
    EmailOutbox,
    ContentSample,
    NodeRole,
    NetMessage,
)
from .tasks import capture_node_screenshot, sample_clipboard
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes


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
                "mac_address": "00:11:22:33:44:55",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Node.objects.count(), 1)

        # allow same IP with different MAC
        self.client.post(
            reverse("register-node"),
            data={
                "hostname": "local2",
                "address": "127.0.0.1",
                "port": 8001,
                "mac_address": "00:11:22:33:44:66",
            },
            content_type="application/json",
        )
        self.assertEqual(Node.objects.count(), 2)

        # duplicate MAC should not create new node
        dup = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "dup",
                "address": "127.0.0.2",
                "port": 8002,
                "mac_address": "00:11:22:33:44:55",
            },
            content_type="application/json",
        )
        self.assertEqual(Node.objects.count(), 2)
        self.assertIn("already exists", dup.json()["detail"])
        self.assertEqual(dup.json()["id"], response.json()["id"])

        list_resp = self.client.get(reverse("node-list"))
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data["nodes"]), 2)
        hostnames = {n["hostname"] for n in data["nodes"]}
        self.assertEqual(hostnames, {"dup", "local2"})

    def test_register_node_has_lcd_screen_toggle(self):
        url = reverse("register-node")
        first = self.client.post(
            url,
            data={
                "hostname": "lcd",
                "address": "127.0.0.1",
                "port": 8000,
                "mac_address": "00:aa:bb:cc:dd:ee",
                "has_lcd_screen": True,
            },
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        node = Node.objects.get(mac_address="00:aa:bb:cc:dd:ee")
        self.assertTrue(node.has_lcd_screen)

        self.client.post(
            url,
            data={
                "hostname": "lcd",
                "address": "127.0.0.1",
                "port": 8000,
                "mac_address": "00:aa:bb:cc:dd:ee",
                "has_lcd_screen": False,
            },
            content_type="application/json",
        )
        node.refresh_from_db()
        self.assertFalse(node.has_lcd_screen)


class NodeRegisterCurrentTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()
        self.user = User.objects.create_user(username="nodeuser", password="pwd")
        self.client.force_login(self.user)
    def test_register_current_sets_and_retains_lcd(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            (locks / "lcd_screen.lck").touch()
            with override_settings(BASE_DIR=base):
                with patch("nodes.models.Node.get_current_mac", return_value="00:ff:ee:dd:cc:bb"), patch(
                    "nodes.models.socket.gethostname", return_value="testhost"
                ), patch(
                    "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                ), patch(
                    "nodes.models.revision.get_revision", return_value="rev"
                ), patch.object(Node, "ensure_keys"):
                    node, created = Node.register_current()
            self.assertTrue(created)
            self.assertTrue(node.has_lcd_screen)

            node.has_lcd_screen = False
            node.save(update_fields=["has_lcd_screen"])

            with override_settings(BASE_DIR=base):
                with patch("nodes.models.Node.get_current_mac", return_value="00:ff:ee:dd:cc:bb"), patch(
                    "nodes.models.socket.gethostname", return_value="testhost"
                ), patch(
                    "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                ), patch(
                    "nodes.models.revision.get_revision", return_value="rev"
                ), patch.object(Node, "ensure_keys"):
                    node2, created2 = Node.register_current()
            self.assertFalse(created2)
            node.refresh_from_db()
            self.assertFalse(node.has_lcd_screen)

    @patch("nodes.views.capture_screenshot")
    def test_capture_screenshot(self, mock_capture):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
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
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.method, "GET")

    @patch("nodes.views.capture_screenshot")
    def test_duplicate_screenshot_skipped(self, mock_capture):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "dup.png"
        file_path.write_bytes(b"dup")
        mock_capture.return_value = Path("screenshots/dup.png")
        self.client.get(reverse("node-screenshot"))
        self.client.get(reverse("node-screenshot"))
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )

    @patch("nodes.views.capture_screenshot")
    def test_capture_screenshot_error(self, mock_capture):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        mock_capture.side_effect = RuntimeError("fail")
        response = self.client.get(reverse("node-screenshot"))
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["detail"], "fail")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 0
        )

    def test_public_api_get_and_post(self):
        node = Node.objects.create(
            hostname="public",
            address="127.0.0.1",
            port=8001,
            enable_public_api=True,
            mac_address="00:11:22:33:44:77",
        )
        url = reverse("node-public-endpoint", args=[node.public_endpoint])

        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["hostname"], "public")

        post_resp = self.client.post(
            url, data="hello", content_type="text/plain"
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertEqual(NetMessage.objects.count(), 1)
        msg = NetMessage.objects.first()
        self.assertEqual(msg.body, "hello")

    def test_public_api_disabled(self):
        node = Node.objects.create(
            hostname="nopublic",
            address="127.0.0.2",
            port=8002,
            mac_address="00:11:22:33:44:88",
        )
        url = reverse("node-public-endpoint", args=[node.public_endpoint])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_net_message_requires_signature(self):
        payload = {
            "uuid": str(uuid.uuid4()),
            "subject": "s",
            "body": "b",
            "seen": [],
            "sender": str(uuid.uuid4()),
        }
        resp = self.client.post(
            reverse("net-message"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_net_message_with_valid_signature(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        sender = Node.objects.create(
            hostname="sender",
            address="10.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:cc",
            public_key=public_key,
        )
        msg_id = str(uuid.uuid4())
        payload = {
            "uuid": msg_id,
            "subject": "hello",
            "body": "world",
            "seen": [],
            "sender": str(sender.uuid),
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = key.sign(
            payload_json.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        resp = self.client.post(
            reverse("net-message"),
            data=payload_json,
            content_type="application/json",
            HTTP_X_SIGNATURE=base64.b64encode(signature).decode(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(NetMessage.objects.filter(uuid=msg_id).exists())

    def test_clipboard_polling_creates_task(self):
        node = Node.objects.create(
            hostname="clip",
            address="127.0.0.1",
            port=9000,
            mac_address="00:11:22:33:44:99",
        )
        task_name = f"poll_clipboard_node_{node.pk}"
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())
        node.clipboard_polling = True
        node.save()
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        node.clipboard_polling = False
        node.save()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_screenshot_polling_creates_task(self):
        node = Node.objects.create(
            hostname="shot",
            address="127.0.0.1",
            port=9100,
            mac_address="00:11:22:33:44:aa",
        )
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

    def tearDown(self):
        security_dir = Path(settings.BASE_DIR) / "security"
        if security_dir.exists():
            shutil.rmtree(security_dir)

    def test_register_current_host(self):
        url = reverse("admin:nodes_node_register_current")
        hostname = socket.gethostname()
        with patch("utils.revision.get_revision", return_value="abcdef123456"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/nodes/node/register_remote.html")
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.first()
        ver = Path('VERSION').read_text().strip()
        rev = "abcdef123456"
        self.assertEqual(node.base_path, str(settings.BASE_DIR))
        self.assertEqual(node.installed_version, ver)
        self.assertEqual(node.installed_revision, rev)
        self.assertEqual(node.mac_address, Node.get_current_mac())
        sec_dir = Path(settings.BASE_DIR) / "security"
        priv = sec_dir / f"{node.public_endpoint}"
        pub = sec_dir / f"{node.public_endpoint}.pub"
        self.assertTrue(sec_dir.exists())
        self.assertTrue(priv.exists())
        self.assertTrue(pub.exists())
        self.assertTrue(node.public_key)
        self.assertTrue(
            Site.objects.filter(domain=hostname, name="host").exists()
        )

    def test_register_current_updates_existing_node(self):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=None,
        )

        response = self.client.get(
            reverse("admin:nodes_node_register_current"), follow=False
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.first()
        self.assertEqual(node.mac_address, Node.get_current_mac())
        self.assertEqual(node.hostname, hostname)

    def test_public_key_download_link(self):
        self.client.get(reverse("admin:nodes_node_register_current"))
        node = Node.objects.first()
        change_url = reverse("admin:nodes_node_change", args=[node.pk])
        response = self.client.get(change_url)
        download_url = reverse("admin:nodes_node_public_key", args=[node.pk])
        self.assertContains(response, download_url)
        resp = self.client.get(download_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Disposition"],
            f'attachment; filename="{node.public_endpoint}.pub"',
        )
        self.assertIn(node.public_key.strip(), resp.content.decode())

    @patch("nodes.admin.capture_screenshot")
    def test_capture_site_screenshot_from_admin(
        self, mock_capture_screenshot
    ):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"admin")
        mock_capture_screenshot.return_value = Path("screenshots/test.png")
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_contentsample_capture")
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "ADMIN")
        mock_capture_screenshot.assert_called_once_with("http://testserver/")
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
        screenshot = ContentSample.objects.create(
            path="screenshots/test.png", kind=ContentSample.IMAGE
        )
        url = reverse("admin:nodes_contentsample_change", args=[screenshot.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data:image/png;base64")

    @override_settings(SCREENSHOT_SOURCES=["/one", "/two"])
    @patch("nodes.admin.capture_screenshot")
    def test_take_screenshots_action(self, mock_capture):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file1 = screenshot_dir / "one.png"
        file1.write_bytes(b"1")
        file2 = screenshot_dir / "two.png"
        file2.write_bytes(b"2")
        mock_capture.side_effect = [
            Path("screenshots/one.png"),
            Path("screenshots/two.png"),
        ]
        node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_node_changelist")
        resp = self.client.post(
            url,
            {"action": "take_screenshots", "_selected_action": [str(node.pk)]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 2
        )
        samples = list(ContentSample.objects.filter(kind=ContentSample.IMAGE))
        self.assertEqual(samples[0].transaction_uuid, samples[1].transaction_uuid)


class NetMessageAdminTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="netmsg-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_complete_flag_not_editable(self):
        msg = NetMessage.objects.create(subject="s", body="b")
        url = reverse("admin:nodes_netmessage_change", args=[msg.pk])
        data = {"subject": "s2", "body": "b2", "complete": "on", "_save": "Save"}
        self.client.post(url, data)
        msg.refresh_from_db()
        self.assertFalse(msg.complete)
        self.assertEqual(msg.subject, "s2")

    def test_send_action_calls_propagate(self):
        msg = NetMessage.objects.create(subject="s", body="b")
        with patch.object(NetMessage, "propagate") as mock_propagate:
            response = self.client.post(
                reverse("admin:nodes_netmessage_changelist"),
                {"action": "send_messages", "_selected_action": [str(msg.pk)]},
            )
        self.assertEqual(response.status_code, 302)
        mock_propagate.assert_called_once()

class NodeActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="action-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_registry_and_local_execution(self):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )

        class DummyAction(NodeAction):
            display_name = "Dummy Action"

            def execute(self, node, **kwargs):
                DummyAction.executed = node

        try:
            DummyAction.executed = None
            DummyAction.run()
            self.assertEqual(DummyAction.executed, node)
            self.assertIn("dummyaction", NodeAction.registry)
        finally:
            NodeAction.registry.pop("dummyaction", None)

    def test_remote_not_supported(self):
        node = Node.objects.create(
            hostname="remote",
            address="10.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:bb",
        )

        class DummyAction(NodeAction):
            def execute(self, node, **kwargs):
                pass

        try:
            with self.assertRaises(NotImplementedError):
                DummyAction.run(node)
        finally:
            NodeAction.registry.pop("dummyaction", None)

    def test_admin_change_view_lists_actions(self):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )

        class DummyAction(NodeAction):
            display_name = "Dummy Action"

            def execute(self, node, **kwargs):
                pass

        try:
            url = reverse("admin:nodes_node_change", args=[node.pk])
            response = self.client.get(url)
            self.assertContains(response, "Dummy Action")
        finally:
            NodeAction.registry.pop("dummyaction", None)


class StartupNotificationTests(TestCase):
    def test_startup_notification_uses_ip_and_revision(self):
        from nodes.apps import _startup_notification

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3")
            with self.settings(BASE_DIR=tmp_path):
                with patch("nodes.apps.revision.get_revision", return_value="abcdef123456"):
                    with patch("core.notifications.notify") as mock_notify:
                        with patch("nodes.apps.socket.gethostname", return_value="host"):
                            with patch(
                                "nodes.apps.socket.gethostbyname", return_value="1.2.3.4"
                            ):
                                with patch.dict(
                                    os.environ, {"PORT": "9000"}
                                ):
                                    _startup_notification()
                                    time.sleep(0.1)

        mock_notify.assert_called_once()
        args, _ = mock_notify.call_args
        self.assertEqual(args[0], "1.2.3.4:9000")
        self.assertTrue(args[1].startswith("v1.2.3 r"))


class NotificationManagerTests(TestCase):
    def test_send_writes_trimmed_lines(self):
        from core.notifications import NotificationManager

        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lcd_screen.lck"
            lock.touch()
            manager = NotificationManager(lock_file=lock)
            result = manager.send("a" * 70, "b" * 70)
            self.assertTrue(result)
            content = lock.read_text().splitlines()
            self.assertEqual(content[0], "a" * 64)
            self.assertEqual(content[1], "b" * 64)

    def test_send_falls_back_to_gui(self):
        from core.notifications import NotificationManager

        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lcd_screen.lck"
            lock.touch()
            manager = NotificationManager(lock_file=lock)
            manager._gui_display = MagicMock()
            with patch.object(
                manager, "_write_lock_file", side_effect=RuntimeError("boom")
            ):
                result = manager.send("hi", "there")
        self.assertTrue(result)
        manager._gui_display.assert_called_once_with("hi", "there")

    def test_send_uses_gui_when_lock_missing(self):
        from core.notifications import NotificationManager

        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lcd_screen.lck"
            manager = NotificationManager(lock_file=lock)
            manager._gui_display = MagicMock()
            result = manager.send("hi", "there")
        self.assertTrue(result)
        manager._gui_display.assert_called_once_with("hi", "there")

    def test_gui_display_uses_windows_toast(self):
        from core.notifications import NotificationManager

        with patch("core.notifications.sys.platform", "win32"):
            mock_notify = MagicMock()
            with patch(
                "core.notifications.plyer_notification",
                MagicMock(notify=mock_notify),
            ):
                manager = NotificationManager()
                manager._gui_display("hi", "there")
        mock_notify.assert_called_once_with(
            title="Arthexis", message="hi\nthere", timeout=6
        )

    def test_gui_display_logs_when_toast_unavailable(self):
        from core.notifications import NotificationManager

        with patch("core.notifications.sys.platform", "win32"):
            with patch("core.notifications.plyer_notification", None):
                with patch("core.notifications.logger") as mock_logger:
                    manager = NotificationManager()
                    manager._gui_display("hi", "there")
        mock_logger.info.assert_called_once_with("%s %s", "hi", "there")


class ContentSampleTransactionTests(TestCase):
    def test_transaction_uuid_behaviour(self):
        sample1 = ContentSample.objects.create(content="a", kind=ContentSample.TEXT)
        self.assertIsNotNone(sample1.transaction_uuid)
        sample2 = ContentSample.objects.create(
            content="b",
            kind=ContentSample.TEXT,
            transaction_uuid=sample1.transaction_uuid,
        )
        self.assertEqual(sample1.transaction_uuid, sample2.transaction_uuid)
        with self.assertRaises(Exception):
            sample1.transaction_uuid = uuid.uuid4()
            sample1.save()


class ContentSampleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "clipboard_admin", "admin@example.com", "pass"
        )
        self.client.login(username="clipboard_admin", password="pass")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_creates_sample(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:nodes_contentsample_from_clipboard")
        response = self.client.get(url, follow=True)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )
        sample = ContentSample.objects.filter(kind=ContentSample.TEXT).first()
        self.assertEqual(sample.content, "clip text")
        self.assertEqual(sample.user, self.user)
        self.assertIsNone(sample.node)
        self.assertContains(response, "Text sample added from clipboard")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_sets_node_when_local_exists(self, mock_paste):
        mock_paste.return_value = "clip text"
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_contentsample_from_clipboard")
        self.client.get(url, follow=True)
        sample = ContentSample.objects.filter(kind=ContentSample.TEXT).first()
        self.assertIsNotNone(sample.node)

    @patch("pyperclip.paste")
    def test_add_from_clipboard_skips_duplicate(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:nodes_contentsample_from_clipboard")
        self.client.get(url, follow=True)
        resp = self.client.get(url, follow=True)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )
        self.assertContains(resp, "Duplicate sample not created")


class EmailOutboxTests(TestCase):
    def test_node_send_mail_uses_outbox(self):
        node = Node.objects.create(
            hostname="outboxhost",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:aa:bb",
        )
        EmailOutbox.objects.create(
            node=node, host="smtp.example.com", port=25, username="u", password="p"
        )
        with patch("nodes.models.get_connection") as gc, patch(
            "nodes.models.send_mail"
        ) as sm:
            conn = MagicMock()
            gc.return_value = conn
            node.send_mail("sub", "msg", ["to@example.com"])
            gc.assert_called_once_with(
                host="smtp.example.com",
                port=25,
                username="u",
                password="p",
                use_tls=True,
                use_ssl=False,
            )
            sm.assert_called_once_with(
                "sub",
                "msg",
                settings.DEFAULT_FROM_EMAIL,
                ["to@example.com"],
                connection=conn,
            )


class ClipboardTaskTests(TestCase):
    @patch("nodes.tasks.pyperclip.paste")
    def test_sample_clipboard_task_creates_sample(self, mock_paste):
        mock_paste.return_value = "task text"
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        sample_clipboard()
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )
        sample = ContentSample.objects.filter(kind=ContentSample.TEXT).first()
        self.assertEqual(sample.content, "task text")
        self.assertIsNone(sample.user)
        self.assertIsNotNone(sample.node)
        self.assertEqual(sample.node.hostname, "host")
        # Duplicate should not create another sample
        sample_clipboard()
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )

    @patch("nodes.tasks.capture_screenshot")
    def test_capture_node_screenshot_task(self, mock_capture):
        node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"task")
        mock_capture.return_value = Path("screenshots/test.png")
        capture_node_screenshot("http://example.com")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "TASK")

    @patch("nodes.tasks.capture_screenshot")
    def test_capture_node_screenshot_handles_error(self, mock_capture):
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        mock_capture.side_effect = RuntimeError("boom")
        result = capture_node_screenshot("http://example.com")
        self.assertEqual(result, "")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 0
        )


class CaptureScreenshotTests(TestCase):
    @patch("nodes.utils.webdriver.Firefox")
    def test_connection_failure_does_not_raise(self, mock_firefox):
        browser = MagicMock()
        mock_firefox.return_value.__enter__.return_value = browser
        browser.get.side_effect = WebDriverException("boom")
        browser.save_screenshot.return_value = True
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        result = capture_screenshot("http://example.com")
        self.assertEqual(result.parent, screenshot_dir)
        browser.save_screenshot.assert_called_once()


class NodeRoleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "role_admin", "admin@example.com", "pass"
        )
        self.client.login(username="role_admin", password="pass")

    def test_change_role_nodes(self):
        role = NodeRole.objects.create(name="TestRole")
        node1 = Node.objects.create(
            hostname="n1",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:55",
            role=role,
        )
        node2 = Node.objects.create(
            hostname="n2",
            address="127.0.0.2",
            port=8000,
            mac_address="00:11:22:33:44:66",
        )
        url = reverse("admin:nodes_noderole_change", args=[role.pk])
        resp = self.client.get(url)
        self.assertContains(resp, f'<option value="{node1.pk}" selected>')
        post_data = {"name": "TestRole", "description": "", "nodes": [node2.pk]}
        resp = self.client.post(url, post_data, follow=True)
        self.assertRedirects(resp, reverse("admin:nodes_noderole_changelist"))
        node1.refresh_from_db()
        node2.refresh_from_db()
        self.assertIsNone(node1.role)
        self.assertEqual(node2.role, role)


