import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model


class AdminDocsCommandsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="docs", email="docs@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_commands_link_present(self):
        response = self.client.get(reverse("django-admindocs-docroot"))
        self.assertContains(response, '<a href="commands/">')

    def test_notify_command_documented(self):
        response = self.client.get(reverse("django-admindocs-commands"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "notify")
        self.assertContains(response, "subject")
