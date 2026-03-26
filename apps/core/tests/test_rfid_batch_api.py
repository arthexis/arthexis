import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.cards.models import RFID


class RFIDBatchApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="rfid-api-user",
            password="test-pass-123",
        )
        self.client.force_login(self.user)

    def test_get_does_not_expose_command_fields(self):
        tag = RFID.objects.create(
            rfid="A1B2C3D4",
            external_command="do not expose",
            post_auth_command="do not expose either",
        )

        response = self.client.get(reverse("rfid-batch"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()["rfids"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["rfid"], tag.rfid)
        self.assertNotIn("external_command", payload[0])
        self.assertNotIn("post_auth_command", payload[0])

    def test_post_rejects_command_fields(self):
        url = reverse("rfid-batch")
        test_cases = [
            (
                {"rfids": [{"rfid": "AABBCCDD", "external_command": "echo hi"}]},
                ["external_command"],
            ),
            (
                {"rfids": [{"rfid": "EEFF0011", "post_auth_command": "echo bye"}]},
                ["post_auth_command"],
            ),
            (
                {
                    "rfids": [
                        {
                            "rfid": "DEADBEEF",
                            "external_command": "one",
                            "post_auth_command": "two",
                        }
                    ]
                },
                ["external_command", "post_auth_command"],
            ),
        ]
        for payload, expected_fields in test_cases:
            with self.subTest(payload=payload, expected_fields=expected_fields):
                response = self.client.post(
                    url,
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                data = response.json()
                self.assertEqual(
                    data["detail"],
                    "Command fields are not accepted by this endpoint.",
                )
                self.assertEqual(data["fields"], expected_fields)

    def test_post_rejects_entire_batch_without_partial_import(self):
        url = reverse("rfid-batch")
        payload = {
            "rfids": [
                {"rfid": "11223344", "custom_label": "would be imported"},
                {"rfid": "55667788", "external_command": "invalid"},
            ]
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(RFID.objects.filter(rfid="11223344").exists())
        self.assertFalse(RFID.objects.filter(rfid="55667788").exists())
