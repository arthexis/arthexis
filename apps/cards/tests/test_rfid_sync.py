import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from apps.cards.models import RFID
from apps.cards.sync import apply_rfid_payload, serialize_rfid
from apps.energy.models import CustomerAccount
from apps.nodes.models import Node


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
        self.assertEqual(payload["name_key"], "1234")
        self.assertEqual(payload["generated_label"], "")
        self.assertEqual(payload["display_label"], "1234")

    def test_apply_rfid_payload_does_not_update_command_fields(self):
        tag = RFID.objects.create(
            rfid="FACEBEEF",
            external_command="keep me",
            post_auth_command="keep me too",
            validation_action="LOG",
            post_auth_action="NOOP",
        )

        outcome = apply_rfid_payload(
            {
                "rfid": "FACEBEEF",
                "custom_label": "updated",
                "external_command": "new value",
                "post_auth_command": "new post value",
                "validation_action": "REJECT",
                "post_auth_action": "LOG",
            }
        )

        self.assertTrue(outcome.ok)
        tag.refresh_from_db()
        self.assertEqual(tag.custom_label, "updated")
        self.assertEqual(tag.external_command, "keep me")
        self.assertEqual(tag.post_auth_command, "keep me too")
        self.assertEqual(tag.validation_action, "REJECT")
        self.assertEqual(tag.post_auth_action, "LOG")

    def test_apply_rfid_payload_updates_generated_label_when_provided(self):
        outcome = apply_rfid_payload(
            {
                "rfid": "FACEBEEF",
                "generated_label": "CalmCedar123",
            }
        )

        self.assertTrue(outcome.ok)
        tag = outcome.instance
        self.assertIsNotNone(tag)
        self.assertEqual(tag.name_key, "FACE")
        self.assertEqual(tag.generated_label, "CalmCedar123")
        self.assertEqual(tag.display_label, "CalmCedar123")

    def test_apply_rfid_payload_preserves_actions_when_omitted(self):
        tag = RFID.objects.create(
            rfid="BEEFFACE",
            validation_action="REJECT",
            post_auth_action="LOG",
        )

        outcome = apply_rfid_payload(
            {
                "rfid": "BEEFFACE",
                "custom_label": "legacy-peer-update",
            }
        )

        self.assertTrue(outcome.ok)
        tag.refresh_from_db()
        self.assertEqual(tag.custom_label, "legacy-peer-update")
        self.assertEqual(tag.validation_action, "REJECT")
        self.assertEqual(tag.post_auth_action, "LOG")

    def test_rfid_sync_export_writes_authorized_json_bundle(self):
        RFID.objects.create(rfid="AAAABBBB", custom_label="Allowed", allowed=True)
        RFID.objects.create(rfid="CCCCDDDD", custom_label="Denied", allowed=False)

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rfids.json"
            stdout = StringIO()

            call_command(
                "rfid",
                "sync",
                "export",
                str(path),
                "--authorized-only",
                stdout=stdout,
            )

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["format"], "arthexis.rfid.sync")
        self.assertEqual(payload["version"], 1)
        self.assertEqual([entry["rfid"] for entry in payload["rfids"]], ["AAAABBBB"])
        self.assertIn("Exported 1 RFID tags", stdout.getvalue())

    def test_rfid_sync_import_applies_json_bundle_and_links_accounts(self):
        account = CustomerAccount.objects.create(name="FLEET")
        payload = {
            "format": "arthexis.rfid.sync",
            "version": 1,
            "rfids": [
                {
                    "rfid": "FACEBEEF",
                    "custom_label": "Fleet Card",
                    "allowed": True,
                    "color": RFID.BLUE,
                    "kind": RFID.CLASSIC,
                    "customer_account_names": [account.name],
                }
            ],
        }

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rfids.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            stdout = StringIO()

            call_command("rfid", "sync", "import", str(path), stdout=stdout)

        tag = RFID.objects.get(rfid="FACEBEEF")
        self.assertEqual(tag.custom_label, "Fleet Card")
        self.assertEqual(tag.color, RFID.BLUE)
        self.assertEqual(
            list(tag.energy_accounts.values_list("name", flat=True)), ["FLEET"]
        )
        self.assertIn(
            "Imported 1 RFID tags: 1 created, 0 updated, 1 account links",
            stdout.getvalue(),
        )

    def test_rfid_sync_import_records_origin_node_from_bundle(self):
        node = Node.objects.create(
            hostname="peer-node", mac_address="aa:bb:cc:dd:ee:ff"
        )
        payload = {
            "format": "arthexis.rfid.sync",
            "version": 1,
            "source_node": {"hostname": node.hostname},
            "rfids": [{"rfid": "ABCDEF12", "allowed": True}],
        }

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rfids.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            call_command("rfid", "sync", "import", str(path), stdout=StringIO())

        tag = RFID.objects.get(rfid="ABCDEF12")
        self.assertEqual(tag.origin_node, node)
