import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from pathlib import Path
from unittest.mock import patch, call, MagicMock
import socket
import base64
from tempfile import TemporaryDirectory

from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin
from django_celery_beat.models import PeriodicTask
from django.conf import settings
from .admin import RecipeAdmin
from .actions import NodeAction

from .models import (
    Node,
    NodeScreenshot,
    NodeMessage,
    Recipe,
    ScreenSource,
    Step,
    TextPattern,
    TextSample,
    Backup,
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
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
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
        self.assertEqual(NodeScreenshot.objects.count(), 1)

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
        self.assertEqual(NodeScreenshot.objects.count(), 0)

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
        self.assertEqual(NodeMessage.objects.count(), 1)
        msg = NodeMessage.objects.first()
        self.assertEqual(msg.body, "hello")
        self.assertEqual(msg.node, node)

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

    def test_backup_creation(self):
        backup = Backup.objects.create(
            location="backups/test.json", size=1234, report={"objects": 5}
        )
        self.assertEqual(Backup.objects.count(), 1)
        self.assertEqual(backup.size, 1234)
        self.assertEqual(backup.report["objects"], 5)

class NodeAdminTests(TestCase):
    fixtures = ["screen_sources"]

    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="nodes-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_register_current_host(self):
        url = reverse("admin:nodes_node_register_current")
        with patch("utils.revision.get_revision", return_value="abcdef123456"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.first()
        ver = Path('VERSION').read_text().strip()
        rev = "abcdef123456"
        self.assertEqual(node.base_path, str(settings.BASE_DIR))
        self.assertEqual(node.installed_version, ver)
        self.assertEqual(node.installed_revision, rev)
        self.assertEqual(node.mac_address, Node.get_current_mac())

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
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.first()
        self.assertEqual(node.mac_address, Node.get_current_mac())
        self.assertEqual(node.hostname, hostname)

    @patch("nodes.admin.capture_screenshot")
    @patch("nodes.admin.capture_screen")
    def test_capture_site_screenshot_from_admin(
        self, mock_capture_screen, mock_capture_screenshot
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
        url = reverse("admin:nodes_nodescreenshot_capture")
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "ADMIN")
        self.assertEqual(screenshot.origin.name, "Homepage")
        mock_capture_screen.assert_not_called()
        mock_capture_screenshot.assert_called_once_with("http://testserver/")
        self.assertContains(
            response, "Screenshot saved to screenshots/test.png"
        )

    @patch("nodes.admin.capture_screen")
    @patch("nodes.admin.capture_screenshot")
    def test_capture_desktop_screenshot_from_admin(
        self, mock_capture_screenshot, mock_capture_screen
    ):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "desktop.png"
        file_path.write_bytes(b"admin")
        mock_capture_screen.return_value = Path("screenshots/desktop.png")
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_nodescreenshot_capture_desktop")
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/desktop.png")
        self.assertEqual(screenshot.method, "ADMIN")
        self.assertEqual(screenshot.origin.name, "Screen 1")
        mock_capture_screen.assert_called_once_with(1)
        mock_capture_screenshot.assert_not_called()
        self.assertContains(
            response, "Screenshot saved to screenshots/desktop.png"
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

    def test_generate_backup_action_creates_backup(self):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        url = reverse(
            "admin:nodes_node_action", args=[node.pk, "generate-db-backup"]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(Backup.objects.count(), 1)
        backup = Backup.objects.first()
        path = Path(settings.BASE_DIR) / backup.location
        if path.exists():
            path.unlink()



class StartupNotificationTests(TestCase):
    def test_startup_notification_uses_ip_and_revision(self):
        from nodes.apps import _startup_notification

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3")
            with self.settings(BASE_DIR=tmp_path):
                with patch("utils.revision.get_revision", return_value="abcdef123456"):
                    with patch("nodes.notifications.notify") as mock_notify:
                        with patch("nodes.apps.socket.gethostname", return_value="host"):
                            with patch("nodes.apps.socket.gethostbyname", return_value="1.2.3.4"):
                                with patch.dict(os.environ, {"PORT": "9000"}):
                                    _startup_notification()

        mock_notify.assert_called_once_with("1.2.3.4:9000", "v1.2.3 r123456")


class NotificationManagerTests(TestCase):
    def test_send_writes_trimmed_lines(self):
        from .notifications import NotificationManager

        manager = NotificationManager()
        mock_lcd = MagicMock()
        manager.lcd = mock_lcd
        result = manager.send("a" * 20, "b" * 20)
        self.assertTrue(result)
        mock_lcd.clear.assert_called_once()
        mock_lcd.write.assert_any_call(0, 0, "a" * 16)
        mock_lcd.write.assert_any_call(0, 1, "b" * 16)

    def test_send_falls_back_to_gui(self):
        from .notifications import NotificationManager

        manager = NotificationManager()
        manager.lcd = None
        manager._gui_display = MagicMock()
        result = manager.send("hi", "there")
        self.assertFalse(result)
        manager._gui_display.assert_called_once_with("hi", "there")

    def test_send_handles_lcd_exception(self):
        from .notifications import NotificationManager

        mock_lcd = MagicMock()
        mock_lcd.clear.side_effect = RuntimeError("boom")
        manager = NotificationManager()
        manager.lcd = mock_lcd
        manager._gui_display = MagicMock()
        result = manager.send("hi", "there")
        self.assertFalse(result)
        manager._gui_display.assert_called_once_with("hi", "there")

    @patch("nodes.notifications.NotificationManager._init_lcd", return_value=MagicMock())
    def test_send_reinitialises_lcd(self, mock_init):
        from .notifications import NotificationManager

        manager = NotificationManager()
        manager.lcd = None
        result = manager.send("subj", "body")
        self.assertTrue(result)
        self.assertEqual(mock_init.call_count, 2)

class NotificationInitTests(TestCase):
    @patch("nodes.notifications.threading.Thread")
    def test_retries_lcd_initialisation(self, mock_thread):
        mock_thread.return_value.start = lambda: None
        lcd = MagicMock()
        with patch(
            "nodes.notifications.NotificationManager._init_lcd",
            side_effect=[None, lcd],
        ) as mock_init:
            manager = NotificationManager()
            manager.send("subj", "body")
            note = manager.queue.get_nowait()
            fake_time, fake_sleep = _fake_time_factory()
            with patch("nodes.notifications.time.time", fake_time), patch(
                "nodes.notifications.time.sleep", fake_sleep,
            ):
                manager._display(note)
        self.assertIs(manager.lcd, lcd)
        self.assertEqual(mock_init.call_count, 2)
        lcd.write.assert_called()

    @patch("nodes.notifications.threading.Thread")
    def test_gui_notification_when_lcd_unavailable(self, mock_thread):
        mock_thread.return_value.start = lambda: None
        with patch(
            "nodes.notifications.NotificationManager._init_lcd",
            side_effect=[None, None],
        ) as mock_init:
            manager = NotificationManager()
            manager.send("subj", "body")
            note = manager.queue.get_nowait()
            manager._gui_display = MagicMock()
            fake_time, fake_sleep = _fake_time_factory()
            with patch("nodes.notifications.time.time", fake_time), patch(
                "nodes.notifications.time.sleep", fake_sleep,
            ):
                manager._display(note)
        self.assertEqual(mock_init.call_count, 2)
        manager._gui_display.assert_called_once_with(note)



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
        msgs = [m.message for m in response.wsgi_request._messages]
        self.assertIn("Matched 'This is [not] good' -> 'This is very good'", msgs)


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
        sample = TextSample.objects.first()
        self.assertEqual(sample.content, "clip text")
        self.assertFalse(sample.automated)
        self.assertIsNone(sample.node)
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
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        sample_clipboard()
        self.assertEqual(TextSample.objects.count(), 1)
        sample = TextSample.objects.first()
        self.assertEqual(sample.content, "task text")
        self.assertTrue(sample.automated)
        self.assertIsNotNone(sample.node)
        self.assertEqual(sample.node.hostname, "host")
        # Duplicate should not create another sample
        sample_clipboard()
        self.assertEqual(TextSample.objects.count(), 1)

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
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
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
        self.assertEqual(NodeScreenshot.objects.count(), 0)


