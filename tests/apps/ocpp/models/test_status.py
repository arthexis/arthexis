from django.test import TestCase

from apps.ocpp.domain.notifications import record_operational_status
from apps.ocpp.models import OperationalStatusRecord
from tests.apps.ocpp.builders import charger


class OperationalStatusRecordTests(TestCase):
    def test_operational_status_is_recorded_by_kind(self) -> None:
        status = record_operational_status(
            charger=charger("charger-1"),
            kind=OperationalStatusRecord.Kind.FIRMWARE,
            status="Downloaded",
            source_action="FirmwareStatusNotification",
            payload={},
        )

        self.assertEqual(status.kind, OperationalStatusRecord.Kind.FIRMWARE)
        self.assertEqual(status.source_action, "FirmwareStatusNotification")
        self.assertIsNone(status.reported_at)
