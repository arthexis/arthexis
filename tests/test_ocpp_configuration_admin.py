import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ocpp.admin import ConfigurationKeyInlineForm
from ocpp.models import Charger, ChargerConfiguration


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

    def test_change_view_includes_configuration_filter(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-200",
        )
        configuration.replace_configuration_keys(
            [
                {"key": "HeartbeatInterval", "value": 300, "readonly": False},
                {"key": "AuthorizeRemoteTxRequests", "readonly": True},
            ]
        )

        url = reverse("admin:ocpp_chargerconfiguration_change", args=[configuration.pk])
        response = self.client.get(url)

        self.assertContains(response, "config-key-filter")
        self.assertContains(response, "value_input")

    def test_configuration_key_form_disables_missing_value(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-FORM",
        )
        configuration.replace_configuration_keys(
            [
                {
                    "key": "JsonValue",
                    "value": {"interval": 60},
                    "readonly": False,
                },
                {"key": "Empty", "readonly": False},
            ]
        )
        entries = list(configuration.configuration_entries.order_by("position", "id"))
        json_form = ConfigurationKeyInlineForm(instance=entries[0])
        self.assertIn("\"interval\": 60", json_form.fields["value_input"].initial or json_form.initial.get("value_input", ""))
        empty_form = ConfigurationKeyInlineForm(instance=entries[1])
        self.assertTrue(empty_form.fields["value_input"].disabled)

    def test_push_configuration_view_lists_available_chargers(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-PUSH",
        )
        charger = Charger.objects.create(charger_id="EVCS-10")

        url = reverse("admin:ocpp_chargerconfiguration_push", args=[configuration.pk])
        response = self.client.get(url)

        self.assertContains(response, "EVCS-10")
        self.assertContains(response, "name=\"chargers\"")

    @mock.patch("ocpp.admin.ChargerConfigurationAdmin._apply_configuration_to_charger")
    def test_push_configuration_progress_returns_json(self, apply_mock):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-PROGRESS",
        )
        charger = Charger.objects.create(charger_id="EVCS-11")
        apply_mock.return_value = (True, "Applied", True)

        url = reverse(
            "admin:ocpp_chargerconfiguration_push_progress",
            args=[configuration.pk],
        )
        response = self.client.post(url, {"charger": charger.pk})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content.decode(),
            {"ok": True, "message": "Applied", "needs_restart": True},
        )
        apply_mock.assert_called_once()

    @mock.patch("ocpp.admin.ChargerConfigurationAdmin._restart_charger")
    def test_restart_configuration_targets_returns_json(self, restart_mock):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-RESET",
        )
        charger = Charger.objects.create(charger_id="EVCS-12")
        restart_mock.return_value = (True, "Restarted")

        url = reverse(
            "admin:ocpp_chargerconfiguration_push_restart",
            args=[configuration.pk],
        )
        response = self.client.post(url, {"charger": charger.pk})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content.decode(),
            {"ok": True, "message": "Restarted"},
        )
        restart_mock.assert_called_once()
