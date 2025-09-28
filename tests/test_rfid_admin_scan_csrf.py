import json
import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from core.models import RFID


pytestmark = [pytest.mark.feature("rfid-scanner")]


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
        response = self.client.post(reverse("admin:core_rfid_scan"))
        self.assertEqual(response.status_code, 200)


class AdminRfidScanNextTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="scanadmin",
            email="scanadmin@example.com",
            password="password",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("admin:core_rfid_scan_next")

    def post_scan(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_scan_next_post_updates_last_seen_for_existing_tag(self):
        tag = RFID.objects.create(rfid="ABCDEF01")
        self.assertIsNone(tag.last_seen_on)

        response = self.post_scan({"rfid": tag.rfid})

        self.assertEqual(response.status_code, 200)
        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_seen_on)

    def test_scan_next_post_sets_last_seen_when_creating_tag(self):
        response = self.post_scan({"rfid": "11223344"})

        self.assertEqual(response.status_code, 200)
        tag = RFID.objects.get(rfid="11223344")
        self.assertIsNotNone(tag.last_seen_on)
