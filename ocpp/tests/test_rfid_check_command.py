import io
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase

from core.models import RFID


class RFIDCheckCommandTests(TestCase):
    def test_uid_validation_outputs_payload(self):
        with patch(
            "ocpp.management.commands.rfid_check.validate_rfid_value",
            return_value={"rfid": "ABCD", "allowed": True},
        ) as mock_validate:
            out = io.StringIO()
            call_command("rfid_check", "--uid", "abcd", stdout=out)

        mock_validate.assert_called_once_with("abcd", kind=None, endianness=None)
        self.assertIn("\"rfid\": \"ABCD\"", out.getvalue())

    def test_uid_validation_error_raises(self):
        with patch(
            "ocpp.management.commands.rfid_check.validate_rfid_value",
            return_value={"error": "bad"},
        ):
            with self.assertRaisesMessage(CommandError, "bad"):
                call_command("rfid_check", "--uid", "abcd")

    def test_label_lookup_by_id(self):
        tag = RFID.objects.create(rfid="ABCD1234", custom_label="Main")

        with patch(
            "ocpp.management.commands.rfid_check.validate_rfid_value",
            return_value={"rfid": tag.rfid, "label_id": tag.label_id},
        ) as mock_validate:
            out = io.StringIO()
            call_command("rfid_check", "--label", str(tag.label_id), stdout=out)

        mock_validate.assert_called_once_with(
            tag.rfid, kind=tag.kind, endianness=tag.endianness
        )
        self.assertIn(str(tag.label_id), out.getvalue())

    def test_label_lookup_by_custom_label(self):
        tag = RFID.objects.create(rfid="FFFF0001", custom_label="Lobby")

        with patch(
            "ocpp.management.commands.rfid_check.validate_rfid_value",
            return_value={"rfid": tag.rfid, "label_id": tag.label_id},
        ) as mock_validate:
            call_command("rfid_check", "--label", "lobby")

        mock_validate.assert_called_once_with(
            tag.rfid, kind=tag.kind, endianness=tag.endianness
        )

    def test_missing_label_raises(self):
        with self.assertRaisesMessage(CommandError, "No RFID found for label"):
            call_command("rfid_check", "--label", "does-not-exist")

    def test_scan_requires_positive_timeout(self):
        with self.assertRaisesMessage(CommandError, "Timeout must be a positive"):
            call_command("rfid_check", "--scan", "--timeout", "0")

    def test_scan_success(self):
        with patch(
            "ocpp.management.commands.rfid_check.read_rfid",
            return_value={"rfid": "AB12", "label_id": 42},
        ) as mock_read:
            out = io.StringIO()
            call_command("rfid_check", "--scan", stdout=out)

        mock_read.assert_called_once_with(timeout=5.0)
        self.assertIn("\"rfid\": \"AB12\"", out.getvalue())

    def test_scan_error_propagates(self):
        with patch(
            "ocpp.management.commands.rfid_check.read_rfid",
            return_value={"error": "hardware"},
        ):
            with self.assertRaisesMessage(CommandError, "hardware"):
                call_command("rfid_check", "--scan")

    def test_scan_without_detected_rfid_raises(self):
        with patch(
            "ocpp.management.commands.rfid_check.read_rfid",
            return_value={"rfid": None},
        ):
            with self.assertRaisesMessage(CommandError, "No RFID detected"):
                call_command("rfid_check", "--scan")
