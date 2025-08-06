from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import Pattern, Sample


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
