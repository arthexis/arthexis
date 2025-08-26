import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model


class AdminRfidScanCsrfTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="rfidadmin",
            email="rfidadmin@example.com",
            password="password",
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_scan_view_allows_post_without_csrf(self):
        response = self.client.post(reverse("admin:accounts_rfid_scan"))
        self.assertEqual(response.status_code, 200)
