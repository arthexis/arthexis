import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ocpp.models import ChargerConfiguration


class ChargerConfigurationAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="cpadmin",
            email="cpadmin@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_change_view_hides_raw_payload(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CP-42",
            raw_payload={"key": "value"},
        )

        url = reverse("admin:ocpp_chargerconfiguration_change", args=[configuration.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("Download raw JSON", page)
        self.assertNotIn('"key"', page)

    def test_download_raw_payload_returns_file(self):
        raw_payload = {"alpha": 1, "beta": 2}
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CP-21",
            raw_payload=raw_payload,
        )

        url = reverse(
            "admin:ocpp_chargerconfiguration_download_raw",
            args=[configuration.pk],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertEqual(json.loads(response.content.decode("utf-8")), raw_payload)

    def test_download_raw_payload_missing_data_returns_404(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CP-99",
            raw_payload={},
        )

        url = reverse(
            "admin:ocpp_chargerconfiguration_download_raw",
            args=[configuration.pk],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
