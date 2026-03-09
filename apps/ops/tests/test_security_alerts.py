"""Tests for security alert aggregation and sidebar widget rendering."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import timezone

from apps.actions.models import RemoteActionToken
from apps.ops import security_alerts
from apps.ops.models import OperationScreen
from apps.widgets.models import WidgetZone
from apps.widgets.services import render_zone_widgets, sync_registered_widgets


pytestmark = pytest.mark.django_db


def test_build_security_alerts_includes_alerts_from_all_sources(monkeypatch):
    """Regression: aggregator should surface token, credential, and operations alerts."""

    user = get_user_model().objects.create_user(username="security-alerts-user")
    RemoteActionToken.issue_for_user(
        user,
        expires_at=timezone.now() + timedelta(hours=2),
    )
    OperationScreen.objects.create(
        title="Required operation",
        slug="required-operation",
        description="Must be completed.",
        start_url="/admin/",
        is_required=True,
        is_active=True,
    )
    monkeypatch.setattr(
        security_alerts,
        "_CREDENTIAL_RULE_SOURCES",
        (
            security_alerts.DashboardRuleAlertSource(
                evaluator=lambda: {
                    "success": False,
                    "message": "Configure an Email Inbox.",
                },
                remediation_url_name="admin:system-dashboard-rules-report",
            ),
        ),
    )

    alerts = security_alerts.build_security_alerts()

    messages = {alert["message"] for alert in alerts}
    severities = {alert["severity"] for alert in alerts}

    assert any("remote action token" in message.lower() for message in messages)
    assert "Configure an Email Inbox." in messages
    assert any("required operation" in message.lower() for message in messages)
    assert severities == {"error", "warning"}
    assert all(alert["remediation_url"] for alert in alerts)


def test_build_security_alerts_returns_empty_list_when_all_checks_pass(monkeypatch):
    """Return an empty list when no source reports an active alert."""

    monkeypatch.setattr(
        security_alerts,
        "_CREDENTIAL_RULE_SOURCES",
        (
            security_alerts.DashboardRuleAlertSource(
                evaluator=lambda: {"success": True, "message": "All rules met."},
                remediation_url_name="admin:system-dashboard-rules-report",
            ),
        ),
    )

    assert security_alerts.build_security_alerts() == []


def test_security_alert_widget_template_renders_empty_state():
    """Template should render a clear empty-state helper when no alerts exist."""

    html = render_to_string("widgets/security_alerts.html", {"alerts": []})

    assert "No active security alerts." in html


def test_security_alert_widget_template_uses_plain_link_for_remediation():
    """Regression: remediation control should render as a link, not a button-styled anchor."""

    html = render_to_string(
        "widgets/security_alerts.html",
        {
            "alerts": [
                {
                    "severity": "warning",
                    "message": "Token expires soon.",
                    "remediation_url": "/admin/actions/remoteactiontoken/",
                }
            ]
        },
    )

    assert 'href="/admin/actions/remoteactiontoken/"' in html
    assert 'class="button"' not in html


def test_build_security_alerts_isolates_collector_failures(monkeypatch):
    """One collector failure should not suppress alerts from other collectors."""

    def _boom():
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(
        security_alerts,
        "expiring_remote_action_token_alerts",
        _boom,
    )
    monkeypatch.setattr(
        security_alerts,
        "credential_readiness_dashboard_rule_alerts",
        lambda: [
            security_alerts.SecurityAlert(
                severity="error",
                message="Credential issue",
                remediation_url="/admin/system/dashboard-rules-report/",
            )
        ],
    )
    monkeypatch.setattr(
        security_alerts,
        "operations_security_alerts",
        lambda: [
            security_alerts.SecurityAlert(
                severity="warning",
                message="Ops issue",
                remediation_url="/admin/ops/operationscreen/",
            )
        ],
    )

    alerts = security_alerts.build_security_alerts()

    assert [alert["severity"] for alert in alerts] == ["error", "warning"]
    assert [alert["message"] for alert in alerts] == ["Credential issue", "Ops issue"]


def test_security_alert_widget_template_renders_translated_severity_and_colored_badges():
    """Severity label should be translated and rendered with badge classes."""

    html = render_to_string(
        "widgets/security_alerts.html",
        {
            "alerts": [
                {
                    "severity": "error",
                    "message": "Credential readiness failed.",
                    "remediation_url": "/admin/system/dashboard-rules-report/",
                },
                {
                    "severity": "info",
                    "message": "Heads up.",
                    "remediation_url": "/admin/",
                },
            ]
        },
    )

    assert "Error" in html
    assert "Info" in html
    assert "security-alert__severity--error" in html
    assert "security-alert__severity--info" in html


def test_security_alert_widget_registered_path_respects_staff_permissions(monkeypatch):
    """Exercise registered widget rendering pipeline for staff and non-staff users."""

    monkeypatch.setattr(
        security_alerts,
        "build_security_alerts",
        lambda: [
            {
                "severity": "warning",
                "message": "Token expires soon.",
                "remediation_url": "/admin/actions/remoteactiontoken/",
            }
        ],
    )

    sync_registered_widgets()
    request_factory = RequestFactory()
    User = get_user_model()

    staff_user = User.objects.create_user(username="alerts-staff", is_staff=True)
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

    non_staff_user = User.objects.create_user(username="alerts-user", is_staff=False)
    non_staff_request = request_factory.get("/")
    non_staff_request.user = non_staff_user

    rendered_for_non_staff = render_zone_widgets(
        request=non_staff_request,
        zone_slug=WidgetZone.ZONE_SIDEBAR,
    )
    assert all(item.widget.slug != "security-alerts" for item in rendered_for_non_staff)
