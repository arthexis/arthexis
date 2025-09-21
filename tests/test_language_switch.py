import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.sites.models import Site
from django.conf import settings


class LanguageSwitchTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "pages"}
        )

    def test_switch_language_without_csrf(self):
        # Visit the home page to ensure site is set up
        self.client.get(reverse("pages:index"))
        # Submit language change without CSRF token
        resp = self.client.post(
            reverse("set_language"), {"language": "es", "next": "/"}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self.client.cookies.get(settings.LANGUAGE_COOKIE_NAME).value, "es"
        )

    def test_redirect_renders_localized_readme(self):
        """Follow redirect and ensure README updates immediately."""
        # Visit the home page to ensure site is set up
        self.client.get(reverse("pages:index"))
        resp = self.client.post(
            reverse("set_language"), {"language": "it", "next": "/"}, follow=True
        )
        self.assertContains(resp, "Costellazione Arthexis")

    def test_italian_readme_rendered(self):
        """Switch to Italian and ensure the localized README is shown."""
        # Visit the home page to ensure site is set up
        self.client.get(reverse("pages:index"))
        # Change language to Italian
        self.client.post(reverse("set_language"), {"language": "it", "next": "/"})
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, "Costellazione Arthexis")

    def test_german_readme_rendered(self):
        """Switch to German and ensure the localized README is shown."""
        # Visit the home page to ensure site is set up
        self.client.get(reverse("pages:index"))
        # Change language to German
        self.client.post(reverse("set_language"), {"language": "de", "next": "/"})
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, "Arthexis-Konstellation")
