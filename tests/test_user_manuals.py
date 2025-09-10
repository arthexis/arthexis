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
from user_manuals.models import UserManual


class UserManualTests(TestCase):
    def setUp(self):
        UserManual.objects.create(
            slug="test-manual",
            title="Test Manual",
            description="Test description",
            content_html="<p>hi</p>",
            content_pdf="UEZERg==",
        )

    def test_manuals_link_in_docs(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="docs", email="docs@example.com", password="password"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("django-admindocs-docroot"))
        self.assertContains(response, reverse("user_manuals:list"))

    def test_manual_pill_rendered(self):
        response = self.client.get(reverse("user_manuals:list"))
        self.assertContains(
            response, 'badge rounded-pill text-bg-secondary">MAN</span>'
        )
        self.assertContains(response, "Test Manual")
        self.assertContains(response, "Test description")
