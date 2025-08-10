from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nodes.models import Node, NodeScreenshot

from .models import Pattern, Sample
from .tasks import capture_node_screenshot, sample_clipboard


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


class SampleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "clipboard_admin", "admin@example.com", "pass"
        )
        self.client.login(username="clipboard_admin", password="pass")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_creates_sample(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:clipboard_sample_from_clipboard")
        response = self.client.get(url, follow=True)
        self.assertEqual(Sample.objects.count(), 1)
        self.assertEqual(Sample.objects.first().content, "clip text")
        self.assertContains(response, "Sample added from clipboard")


class ClipboardTaskTests(TestCase):
    @patch("clipboard.tasks.pyperclip.paste")
    def test_sample_clipboard_task_creates_sample(self, mock_paste):
        mock_paste.return_value = "task text"
        sample_clipboard()
        self.assertEqual(Sample.objects.count(), 1)
        self.assertEqual(Sample.objects.first().content, "task text")

    @patch("clipboard.tasks.capture_screenshot")
    @patch("clipboard.tasks.socket.gethostname")
    def test_capture_node_screenshot_task(self, mock_hostname, mock_capture):
        mock_hostname.return_value = "host"
        node = Node.objects.create(hostname="host", address="127.0.0.1", port=8000)
        mock_capture.return_value = Path("screenshots/test.png")
        capture_node_screenshot("http://example.com")
        self.assertEqual(NodeScreenshot.objects.count(), 1)
        screenshot = NodeScreenshot.objects.first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
