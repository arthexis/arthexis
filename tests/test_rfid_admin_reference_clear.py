import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import RFID
from core.models import Reference


pytestmark = [pytest.mark.feature("rfid-scanner")]


class RFIDAdminReferenceClearTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="clearref", email="clear@example.com", password="password"
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.reference = Reference.objects.create(alt_text="ref", value="val")
        self.rfid = RFID.objects.create(rfid="AABBCCDD", reference=self.reference)

    def test_reference_can_be_cleared(self):
        url = reverse("admin:core_rfid_change", args=[self.rfid.pk])
        response = self.client.get(url)
        csrf = response.cookies["csrftoken"].value
        data = {
            "csrfmiddlewaretoken": csrf,
            "rfid": self.rfid.rfid,
            "key_a": self.rfid.key_a,
            "key_b": self.rfid.key_b,
            "allowed": "on" if self.rfid.allowed else "",
            "color": self.rfid.color,
            "kind": self.rfid.kind,
            "released": "on" if self.rfid.released else "",
            "reference": "",
            "_save": "Save",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.rfid.refresh_from_db()
        self.assertIsNone(self.rfid.reference)

    def test_reference_widget_has_related_links(self):
        url = reverse("admin:core_rfid_change", args=[self.rfid.pk])
        response = self.client.get(url)
        self.assertContains(response, "related-lookup")
        self.assertContains(response, "add-related")
        self.assertContains(response, "change-related")
