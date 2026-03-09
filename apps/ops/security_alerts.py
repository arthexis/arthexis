"""Security alert aggregation for admin sidebar widgets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from apps.actions.models import RemoteActionToken
from apps.counters import dashboard_rules
from apps.ops.dashboard_rules import evaluate_required_operations_rule

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SecurityAlert:
    """Normalized security alert payload used by widget templates."""

    severity: str
    message: str
    remediation_url: str


@dataclass(frozen=True, slots=True)
class DashboardRuleAlertSource:
    """Mapping of a dashboard-rule evaluator to its remediation destination."""

    evaluator: Callable[[], dict[str, object] | None]
    remediation_url_name: str


_TOKEN_EXPIRY_LOOKAHEAD = timedelta(hours=6)

_CREDENTIAL_RULE_SOURCES: tuple[DashboardRuleAlertSource, ...] = (
    DashboardRuleAlertSource(
        evaluator=dashboard_rules.evaluate_aws_credentials_rules,
        remediation_url_name="admin:system-dashboard-rules-report",
    ),
    DashboardRuleAlertSource(
        evaluator=dashboard_rules.evaluate_email_profile_rules,
        remediation_url_name="admin:system-dashboard-rules-report",
    ),
)


def _reverse_or_fallback(url_name: str, fallback: str) -> str:
    """Resolve ``url_name`` or return ``fallback`` when unavailable."""

    try:
        return reverse(url_name)
    except NoReverseMatch:
        return fallback


def expiring_remote_action_token_alerts(*, now=None) -> list[SecurityAlert]:
    """Return alerts for active remote action tokens nearing expiry."""

    current_time = now or timezone.now()
    threshold = current_time + _TOKEN_EXPIRY_LOOKAHEAD
    expiring_count = RemoteActionToken.objects.filter(
        is_active=True,
        expires_at__gt=current_time,
        expires_at__lte=threshold,
    ).count()
    if not expiring_count:
        return []

    message = ngettext(
        "%(count)s remote action token expires soon.",
        "%(count)s remote action tokens expire soon.",
        expiring_count,
    ) % {"count": expiring_count}
    return [
        SecurityAlert(
            severity="warning",
            message=str(message),
            remediation_url=_reverse_or_fallback(
                "admin:actions_remoteactiontoken_changelist",
                "/admin/actions/remoteactiontoken/",
            ),
        )
    ]


def credential_readiness_dashboard_rule_alerts() -> list[SecurityAlert]:
    """Return alerts from known credential-readiness dashboard evaluators."""

    alerts: list[SecurityAlert] = []
    for source in _CREDENTIAL_RULE_SOURCES:
        try:
            result = source.evaluator()
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("Security alert evaluator failed: %s", exc)
            continue
        if not result or result.get("success", True):
            continue
        alerts.append(
            SecurityAlert(
                severity="error",
                message=str(
                    result.get("message")
                    or _("Credential readiness check failed.")
                ),
                remediation_url=_reverse_or_fallback(
                    source.remediation_url_name,
                    "/admin/system/dashboard-rules-report/",
                ),
            )
        )
    return alerts


def operations_security_alerts() -> list[SecurityAlert]:
    """Return alerts based on operations compliance checks."""

    result = evaluate_required_operations_rule()
    if result.get("success", True):
        return []

    return [
        SecurityAlert(
            severity="warning",
            message=str(
                result.get("message")
                or _("Required operations check failed.")
            ),
            remediation_url=_reverse_or_fallback(
                "admin:ops_operationscreen_changelist",
                "/admin/ops/operationscreen/",
            ),
        )
    ]


def build_security_alerts() -> list[dict[str, str]]:
    """Aggregate security alerts from all supported data sources."""

    alerts: list[SecurityAlert] = []
    for source_name, collector in (
        ("remote_action_tokens", expiring_remote_action_token_alerts),
        ("credential_readiness", credential_readiness_dashboard_rule_alerts),
        ("required_operations", operations_security_alerts),
    ):
        try:
            alerts.extend(collector())
        except Exception:
            logger.exception("Security alert source %s failed", source_name)

    severity_order = {"error": 0, "warning": 1, "info": 2}
    return [
        {
            "severity": alert.severity,
            "message": alert.message,
            "remediation_url": alert.remediation_url,
        }
        for alert in sorted(
            alerts,
            key=lambda alert: severity_order.get(alert.severity, 99),
        )
    ]
