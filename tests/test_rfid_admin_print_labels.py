import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import RFID


pytestmark = [pytest.mark.feature("rfid-scanner")]


class RFIDAdminPrintLabelsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="labeller",
            email="labels@example.com",
            password="password",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("admin:core_rfid_changelist")

    def test_print_card_labels_returns_pdf_response(self):
        tag1 = RFID.objects.create(rfid="ABCDEF01")
        tag2 = RFID.objects.create(rfid="12345678", custom_label="Lobby")

        response = self.client.post(
            self.url,
            data={
                "action": "print_card_labels",
                ACTION_CHECKBOX_NAME: [str(tag1.pk), str(tag2.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(
            response["Content-Disposition"].startswith(
                "attachment; filename=rfid-card-labels"
            )
        )
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 1000)
