"""Tests for security alert aggregation and sidebar widget rendering."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils import timezone

from apps.actions.models import RemoteActionToken
from apps.ops import security_alerts
from apps.ops.models import OperationScreen


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
