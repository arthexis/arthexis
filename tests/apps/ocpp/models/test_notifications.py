from datetime import datetime, timezone

from django.test import TestCase

from apps.ocpp.domain.notifications import record_monitoring, record_notification
from tests.apps.ocpp.builders import charger


class NotificationModelTests(TestCase):
    def test_notification_and_monitoring_records_retain_safe_payloads(self) -> None:
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

        self.assertEqual(notification.action, "NotifyEvent")
        self.assertEqual(
            notification.reported_at,
            datetime(2026, 9, 22, 18, tzinfo=timezone.utc),
        )
        self.assertEqual(monitoring.severity, 2)
        self.assertEqual(
            monitoring.reported_at,
            datetime(2026, 9, 22, 18, 5, tzinfo=timezone.utc),
        )
