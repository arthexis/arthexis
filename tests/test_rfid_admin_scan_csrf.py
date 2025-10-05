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

    def test_scan_assigns_labels_in_steps_of_ten(self):
        self.post_scan({"rfid": "AA11BB22"})
        first = RFID.objects.get(rfid="AA11BB22")
        self.assertEqual(first.label_id, 10)

        self.post_scan({"rfid": "CC33DD44"})
        second = RFID.objects.get(rfid="CC33DD44")
        self.assertEqual(second.label_id, 20)

    def test_scan_ignores_non_multiple_labels(self):
        RFID.objects.create(label_id=21, rfid="EE55FF66")

        self.post_scan({"rfid": "1122AABB"})
        created = RFID.objects.get(rfid="1122AABB")
        self.assertEqual(created.label_id, 30)


class AdminRfidCopyActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="copyadmin",
            email="copyadmin@example.com",
            password="password",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("admin:core_rfid_changelist")

    def _start_copy(self, tag, new_rfid):
        response = self.client.post(
            self.url,
            data={"action": "copy_rfids", "_selected_action": [tag.pk]},
        )
        self.assertEqual(response.status_code, 200)
        return self.client.post(
            self.url,
            data={
                "action": "copy_rfids",
                "_selected_action": [tag.pk],
                "apply": "1",
                "rfid": new_rfid,
            },
        )

    def test_copy_action_increments_label_by_one(self):
        original = RFID.objects.create(label_id=20, rfid="AABBCCDD", custom_label="Lobby")

        response = self._start_copy(original, "DDCCBBAA")

        self.assertEqual(response.status_code, 302)
        copied = RFID.objects.get(rfid="DDCCBBAA")
        self.assertEqual(copied.label_id, 21)
        self.assertEqual(copied.custom_label, original.custom_label)
        self.assertEqual(copied.color, original.color)

    def test_copy_action_skips_existing_label(self):
        original = RFID.objects.create(label_id=30, rfid="FACE1234")
        RFID.objects.create(label_id=31, rfid="BEEF5678")

        response = self._start_copy(original, "FEEDC0DE")

        self.assertEqual(response.status_code, 302)
        copied = RFID.objects.get(rfid="FEEDC0DE")
        self.assertEqual(copied.label_id, 32)
