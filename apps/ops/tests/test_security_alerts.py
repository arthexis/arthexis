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


def test_build_security_alerts_returns_empty_list_when_no_error_events() -> None:
    """Return an empty list when there are no active event alerts."""

    assert security_alerts.build_security_alerts() == []


def test_security_alert_widget_template_renders_empty_state() -> None:
    """Template should render a clear empty-state helper when no alerts exist."""

    html = render_to_string("widgets/security_alerts.html", {"alerts": []})

    assert "No active security alerts." in html


def test_security_alert_widget_template_uses_plain_link_for_remediation() -> None:
    """Regression: remediation control should render as a plain link."""

    html = render_to_string(
        "widgets/security_alerts.html",
        {
            "alerts": [
                {
                    "severity": "warning",
                    "message": "Token expires soon.",
                    "summary": "Last seen: 2026-01-01 10:00:00 · Count: 5",
                    "remediation_url": "/admin/actions/remoteactiontoken/",
                }
            ]
        },
    )

    assert 'href="/admin/actions/remoteactiontoken/"' in html
    assert 'class="button"' not in html
    assert "Last seen:" in html


def test_build_security_alerts_isolates_collector_failures(monkeypatch) -> None:
    """Collector failures should not crash rendering and should be recorded."""

    def _boom(now=None) -> list[security_alerts.SecurityAlert]:
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(security_alerts, "error_event_security_alerts", _boom)

    alerts = security_alerts.build_security_alerts()

    assert alerts == []
    persisted_event = SecurityAlertEvent.objects.get(key="security-alert-source-error-events")
    assert persisted_event.occurrence_count == 1


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


def test_build_security_alerts_clears_collector_failure_on_success(monkeypatch) -> None:
    """Collector failure events should be marked inactive after successful collection."""

    SecurityAlertEvent.record_occurrence(
        key="security-alert-source-error-events",
        message="Security alert source failed.",
        detail="error_events: boom",
        remediation_url="/admin/system/dashboard-rules-report/",
    )

    monkeypatch.setattr(security_alerts, "error_event_security_alerts", lambda now=None: [])

    alerts = security_alerts.build_security_alerts()

    assert alerts == []
    persisted_event = SecurityAlertEvent.objects.get(key="security-alert-source-error-events")
    assert persisted_event.is_active is False


def test_security_alert_widget_template_renders_translated_severity_and_colored_badges() -> None:
    """Severity label should be translated and rendered with badge classes."""

    html = render_to_string(
        "widgets/security_alerts.html",
        {
            "alerts": [
                {
                    "severity": "error",
                    "message": "Credential readiness failed.",
                    "summary": "Last seen: 2026-01-01 10:00:00 · Count: 2",
                    "remediation_url": "/admin/system/dashboard-rules-report/",
                },
                {
                    "severity": "info",
                    "message": "Heads up.",
                    "summary": "",
                    "remediation_url": "/admin/",
                },
            ]
        },
    )

    assert "Error" in html
    assert "Info" in html
    assert "security-alert__severity--error" in html
    assert "security-alert__severity--info" in html


def test_security_alert_widget_registered_path_respects_staff_permissions(monkeypatch) -> None:
    """Exercise registered widget rendering pipeline for staff and non-staff users."""

    monkeypatch.setattr(
        ops_widgets,
        "build_security_alerts",
        lambda: [
            {
                "severity": "warning",
                "message": "Token expires soon.",
                "summary": "Last seen: 2026-01-01 10:00:00 · Count: 1",
                "remediation_url": "/admin/actions/remoteactiontoken/",
            }
        ],
    )

    sync_registered_widgets()
    request_factory = RequestFactory()
    user_model = get_user_model()

    staff_user = user_model.objects.create_user(username="alerts-staff", is_staff=True)
    staff_request = request_factory.get("/")
    staff_request.user = staff_user

    rendered_for_staff = render_zone_widgets(
        request=staff_request,
        zone_slug=WidgetZone.ZONE_SIDEBAR,
    )

    security_widget_html = [
        item.html
        for item in rendered_for_staff
        if item.widget.slug == "security-alerts"
    ]
    assert security_widget_html
    assert 'href="/admin/actions/remoteactiontoken/"' in security_widget_html[0]
    assert 'class="button"' not in security_widget_html[0]

    non_staff_user = user_model.objects.create_user(username="alerts-user", is_staff=False)
    non_staff_request = request_factory.get("/")
    non_staff_request.user = non_staff_user

    rendered_for_non_staff = render_zone_widgets(
        request=non_staff_request,
        zone_slug=WidgetZone.ZONE_SIDEBAR,
    )
    assert all(item.widget.slug != "security-alerts" for item in rendered_for_non_staff)


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
