import pytest
from django.contrib.admin.sites import site

from apps.ocpp.admin.operations import ProtocolOperationAdmin
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_admin_exposes_safe_terminal_outcome_without_protocol_payloads() -> None:
    operation = ProtocolOperation.objects.create(
        charger=charger("charger-1"),
        version=ProtocolVersion.OCPP_16,
        direction=Direction.CSMS_TO_CHARGE_POINT,
        action="Reset",
        request_payload={"sensitive": "never-render"},
        error_code="NotSupported",
    )
    admin = site._registry[ProtocolOperation]

    assert isinstance(admin, ProtocolOperationAdmin)
    assert "completed_at" in admin.list_display
    assert "terminal_outcome" in admin.list_display
    assert "request_payload" not in admin.fields
    assert "response_payload" not in admin.fields
    assert admin.terminal_outcome(operation) == "NotSupported"
