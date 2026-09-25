from datetime import datetime, timezone

import pytest

from apps.ocpp.domain.notifications import record_monitoring, record_notification
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_notification_and_monitoring_records_retain_safe_payloads() -> None:
    selected = charger("charger-1")
    notification = record_notification(
        charger=selected,
        action="NotifyEvent",
        payload={"eventData": []},
        reported_at=datetime(2026, 9, 22, 18, tzinfo=timezone.utc),
    )
    monitoring = record_monitoring(
        charger=selected,
        event_type="Threshold",
        payload={"value": 3},
        severity=2,
        reported_at=datetime(2026, 9, 22, 18, 5, tzinfo=timezone.utc),
    )

    assert notification.action == "NotifyEvent"
    assert notification.reported_at == datetime(2026, 9, 22, 18, tzinfo=timezone.utc)
    assert monitoring.severity == 2
    assert monitoring.reported_at == datetime(2026, 9, 22, 18, 5, tzinfo=timezone.utc)
