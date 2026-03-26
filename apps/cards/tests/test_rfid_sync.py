from django.test import TestCase

from apps.cards.models import RFID
from apps.cards.sync import apply_rfid_payload, serialize_rfid


class RFIDSyncContractTests(TestCase):
    def test_serialize_rfid_excludes_command_fields(self):
        tag = RFID.objects.create(
            rfid="1234ABCD",
            external_command="private command",
            post_auth_command="private post command",
        )

        payload = serialize_rfid(tag)

        self.assertNotIn("external_command", payload)
        self.assertNotIn("post_auth_command", payload)

    def test_apply_rfid_payload_does_not_update_command_fields(self):
        tag = RFID.objects.create(
            rfid="FACEBEEF",
            external_command="keep me",
            post_auth_command="keep me too",
        )

        outcome = apply_rfid_payload(
            {
                "rfid": "FACEBEEF",
                "custom_label": "updated",
                "external_command": "new value",
                "post_auth_command": "new post value",
            }
        )

        self.assertTrue(outcome.ok)
        tag.refresh_from_db()
        self.assertEqual(tag.custom_label, "updated")
        self.assertEqual(tag.external_command, "keep me")
        self.assertEqual(tag.post_auth_command, "keep me too")
