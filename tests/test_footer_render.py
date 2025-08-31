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
from django.contrib.auth import get_user_model

from core.models import Reference
from core.release import DEFAULT_PACKAGE
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
        release_name = f"{DEFAULT_PACKAGE.name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        self.assertContains(response, release_name)

    def test_footer_private_visibility(self):
        Reference.objects.create(
            alt_text="Private",
            value="https://private.example.com",
            method="link",
            include_in_footer=True,
            footer_visibility=Reference.FOOTER_PRIVATE,
        )
        response = self.client.get(reverse("pages:login"))
        self.assertNotContains(response, "Private")
        user = get_user_model().objects.create_user(username="u1", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertContains(response, "Private")

    def test_footer_staff_visibility(self):
        Reference.objects.create(
            alt_text="Staff",
            value="https://staff.example.com",
            method="link",
            include_in_footer=True,
            footer_visibility=Reference.FOOTER_STAFF,
        )
        response = self.client.get(reverse("pages:login"))
        self.assertNotContains(response, "Staff")
        user = get_user_model().objects.create_user(username="u2", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, "Staff")
        staff = get_user_model().objects.create_user(
            username="staff", password="x", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("pages:index"))
        self.assertContains(response, "Staff")
