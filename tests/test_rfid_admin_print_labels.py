import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.http import HttpResponse
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

    def test_print_valid_card_labels_returns_pdf_response(self):
        RFID.objects.create(rfid="VALID0001", allowed=True, released=True)
        RFID.objects.create(rfid="VALID0002", allowed=True, released=True)

        response = self.client.get(
            reverse("admin:core_rfid_print_valid_card_labels")
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

    def test_print_valid_card_labels_filters_to_released_and_allowed(self):
        allowed_released = RFID.objects.create(
            rfid="VALIDONLY",
            allowed=True,
            released=True,
        )
        RFID.objects.create(rfid="ALLOWEDONLY", allowed=True, released=False)
        RFID.objects.create(rfid="RELEASEDONLY", allowed=False, released=True)

        with mock.patch(
            "core.admin.RFIDAdmin._render_card_labels",
            autospec=True,
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as render_mock:
            response = self.client.get(
                reverse("admin:core_rfid_print_valid_card_labels")
            )

        self.assertEqual(response.status_code, 200)
        args, _ = render_mock.call_args
        queryset = args[2]
        self.assertQuerySetEqual(
            queryset.values_list("pk", flat=True),
            [allowed_released.pk],
            ordered=False,
        )

    def test_print_valid_card_labels_redirects_with_message_when_empty(self):
        response = self.client.get(
            reverse("admin:core_rfid_print_valid_card_labels"),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No RFID cards marked as valid are available to print.")

    def test_print_release_form_returns_pdf_response(self):
        tag1 = RFID.objects.create(rfid="REL00001", released=True)
        tag2 = RFID.objects.create(rfid="REL00002", custom_label="Front Desk", released=True)

        response = self.client.post(
            self.url,
            data={
                "action": "print_release_form",
                ACTION_CHECKBOX_NAME: [str(tag1.pk), str(tag2.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(
            response["Content-Disposition"].startswith(
                "attachment; filename=rfid-release-form"
            )
        )
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 500)
