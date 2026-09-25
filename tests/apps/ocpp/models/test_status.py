import pytest

from apps.ocpp.domain.notifications import record_operational_status
from apps.ocpp.models import OperationalStatusRecord
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_operational_status_is_recorded_by_kind() -> None:
    status = record_operational_status(
        charger=charger("charger-1"),
        kind=OperationalStatusRecord.Kind.FIRMWARE,
        status="Downloaded",
        source_action="FirmwareStatusNotification",
        payload={},
    )

    assert status.kind == OperationalStatusRecord.Kind.FIRMWARE
    assert status.source_action == "FirmwareStatusNotification"
    assert status.reported_at is None
