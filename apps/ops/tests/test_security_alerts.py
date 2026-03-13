"""Tests for security alert aggregation and sidebar widget rendering."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import timezone

from apps.ops import security_alerts, widgets as ops_widgets
from apps.ops.models import SecurityAlertEvent
from apps.widgets.models import WidgetZone
from apps.widgets.services import render_zone_widgets, sync_registered_widgets


pytestmark = pytest.mark.django_db


def test_build_security_alerts_includes_active_error_events_only() -> None:
    """Aggregator should include active persisted error events only."""

    SecurityAlertEvent.record_occurrence(
        key="event-active",
        message="Active worker failure.",
        detail="traceback",
        remediation_url="/admin/system/dashboard-rules-report/",
    )
    SecurityAlertEvent.record_occurrence(
        key="event-recovered",
        message="Recovered worker failure.",
        detail="traceback",
        remediation_url="/admin/system/dashboard-rules-report/",
    )
    SecurityAlertEvent.clear_occurrence(key="event-recovered")

    alerts = security_alerts.build_security_alerts()

    assert [alert["message"] for alert in alerts] == ["Active worker failure."]
    assert alerts[0]["severity"] == "error"

def test_record_occurrence_keeps_last_occurred_at_monotonic() -> None:
    """Older occurrences should not move last_occurred_at backwards."""

    first_seen = timezone.now()
    SecurityAlertEvent.record_occurrence(
        key="event-monotonic",
        message="Monotonic event.",
        detail="first",
        remediation_url="/admin/system/dashboard-rules-report/",
        occurred_at=first_seen,
    )
    SecurityAlertEvent.record_occurrence(
        key="event-monotonic",
        message="Monotonic event.",
        detail="older replay",
        remediation_url="/admin/system/dashboard-rules-report/",
        occurred_at=first_seen - timedelta(minutes=5),
    )

    event = SecurityAlertEvent.objects.get(key="event-monotonic")
    assert event.occurrence_count == 2
    assert event.last_occurred_at == first_seen

def test_error_event_security_alerts_surface_last_seen_and_count() -> None:
    """Recorded event alerts should expose summary with timestamp and occurrence count."""

    event = SecurityAlertEvent.record_occurrence(
        key="event-email-worker",
        message="Email worker failed.",
        detail="smtp timeout",
        remediation_url="/admin/system/dashboard-rules-report/",
    )
    SecurityAlertEvent.record_occurrence(
        key="event-email-worker",
        message="Email worker failed.",
        detail="smtp timeout",
        remediation_url="/admin/system/dashboard-rules-report/",
    )
    event.refresh_from_db()

    alerts = security_alerts.error_event_security_alerts(
        now=event.last_occurred_at + timedelta(minutes=1)
    )

    assert len(alerts) == 1
    assert alerts[0].message == "Email worker failed."
    assert "Count: 2" in alerts[0].summary
