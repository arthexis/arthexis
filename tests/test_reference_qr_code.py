import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Reference

TMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP_MEDIA_ROOT)
class ReferenceQRTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="qradm",
            email="qr@example.com",
            password="password",
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_qr_code_generated_for_reference_without_image(self):
        ref = Reference.objects.create(
            alt_text="ref",
            method="text",
            value="some value",
        )
        self.assertTrue(ref.image)

    def test_qr_code_displayed_in_admin_change(self):
        ref = Reference.objects.create(
            alt_text="ref",
            method="text",
            value="some value",
        )
        url = reverse("admin:pages_experiencereference_change", args=[ref.pk])
        response = self.client.get(url)
        self.assertContains(response, ref.image.url)
        self.assertContains(response, "<img", html=False)
