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
from nodes.models import Node, NodeRole
from pages.models import Application, Module


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

    def test_fallback_uses_root_localized_readme(self):
        role = NodeRole.objects.create(name="Role")
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            mac_address=Node.get_current_mac(),
            role=role,
        )
        app = Application.objects.create(name="foo")
        Module.objects.create(
            node_role=role, application=app, path="/foo/", is_default=True
        )
        self.client.post("/i18n/setlang/", {"language": "de", "next": "/"})
        response = self.client.get("/")
        self.assertContains(response, "Arthexis-Konstellation")
