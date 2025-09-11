import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.sites.models import Site
from django.test import TestCase, RequestFactory
from pages.views import index


class ReadmeLanguageTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(
            domain="testserver", defaults={"name": "testserver"}
        )
        self.factory = RequestFactory()

    def test_spanish_readme_selected(self):
        self.client.post("/i18n/setlang/", {"language": "es", "next": "/"})
        response = self.client.get("/")
        self.assertContains(response, "Constelación Arthexis")

    def test_vary_headers_present(self):
        response = self.client.get("/")
        vary = response.headers.get("Vary", "")
        self.assertIn("Accept-Language", vary)
        self.assertIn("Cookie", vary)

    def test_cache_headers_prevent_stale_readme(self):
        response = self.client.get("/")
        cache = response.headers.get("Cache-Control", "")
        self.assertIn("no-store", cache)

    def test_language_code_case_insensitive(self):
        request = self.factory.get("/")
        request.LANGUAGE_CODE = "ES"
        response = index(request)
        self.assertContains(response, "Constelación Arthexis")
