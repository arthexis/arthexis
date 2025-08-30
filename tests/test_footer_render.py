import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Reference
from utils import revision

TMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP_MEDIA_ROOT)
class FooterRenderTests(TestCase):
    def setUp(self):
        Reference.objects.create(
            alt_text="Example",
            value="https://example.com",
            method="link",
            include_in_footer=True,
        )
        self.client = Client()

    def test_footer_contains_reference(self):
        response = self.client.get(reverse("pages:login"))
        self.assertContains(response, "<footer", html=False)
        self.assertContains(response, "Example")
        self.assertContains(response, "https://example.com")
        version = Path("VERSION").read_text().strip()
        rev_short = revision.get_revision()[-6:]
        self.assertContains(response, f"v{version}")
        if rev_short:
            self.assertContains(response, f"r{rev_short}")
