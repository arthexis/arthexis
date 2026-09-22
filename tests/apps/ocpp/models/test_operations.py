from django.test import TestCase

from apps.ocpp.domain.operations import complete_operation, create_operation
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger


class ProtocolOperationModelTests(TestCase):
    def test_domain_service_persists_completed_operation(self) -> None:
        operation = create_operation(
            charger=charger("charger-1"),
            version=ProtocolVersion.OCPP_201,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetVariables",
            request_payload={"getVariableData": []},
        )
        complete_operation(operation, response_payload={"getVariableResult": []})

        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
