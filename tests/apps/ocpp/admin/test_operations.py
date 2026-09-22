from django.contrib.admin.sites import site
from django.test import TestCase

from apps.ocpp.admin.operations import ProtocolOperationAdmin
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger


class ProtocolOperationAdminTests(TestCase):
    def test_admin_exposes_safe_terminal_outcome_without_protocol_payloads(
        self,
    ) -> None:
        operation = ProtocolOperation.objects.create(
            charger=charger("charger-1"),
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="Reset",
            request_payload={"sensitive": "never-render"},
            error_code="NotSupported",
        )
        admin = site._registry[ProtocolOperation]

        self.assertIsInstance(admin, ProtocolOperationAdmin)
        self.assertIn("completed_at", admin.list_display)
        self.assertIn("terminal_outcome", admin.list_display)
        self.assertNotIn("request_payload", admin.fields)
        self.assertNotIn("response_payload", admin.fields)
        self.assertEqual(admin.terminal_outcome(operation), "NotSupported")
