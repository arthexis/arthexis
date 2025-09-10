import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.sites.models import Site
from django.test import TestCase


class ReadmeLanguageTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(
            domain="testserver", defaults={"name": "testserver"}
        )

    def test_spanish_readme_selected(self):
        response = self.client.get("/", HTTP_ACCEPT_LANGUAGE="es")
        self.assertContains(response, "Constelación Arthexis")

    def test_vary_headers_present(self):
        response = self.client.get("/")
        vary = response.headers.get("Vary", "")
        self.assertIn("Accept-Language", vary)
        self.assertIn("Cookie", vary)
