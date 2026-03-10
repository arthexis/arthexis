"""Security alert aggregation for admin sidebar widgets."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.ops.models import SecurityAlertEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SecurityAlert:
    """Normalized security alert payload used by widget templates."""

    severity: str
    message: str
    remediation_url: str
    summary: str = ""


_COLLECTOR_EVENT_KEYS: dict[str, str] = {
    "error_events": "security-alert-source-error-events",
}


_COLLECTOR_EVENT_KEYS: dict[str, str] = {
    "error_events": "security-alert-source-error-events",
}


def _reverse_or_fallback(url_name: str, fallback: str) -> str:
    """Resolve ``url_name`` or return ``fallback`` when unavailable."""

    try:
        return reverse(url_name)
    except NoReverseMatch:
        return fallback


def _record_collector_failure_event(*, source_name: str, detail: str) -> None:
    """Persist collector failures so repeated runtime errors appear in the widget."""

    event_key = _COLLECTOR_EVENT_KEYS.get(source_name)
    if not event_key:
        return

    try:
        SecurityAlertEvent.record_occurrence(
            key=event_key,
            message=_("Security alert source failed."),
            detail=f"{source_name}: {detail}",
            remediation_url=_reverse_or_fallback(
                "admin:system-dashboard-rules-report",
                "/admin/system/dashboard-rules-report/",
            ),
        )
    except Exception:
        logger.debug("Unable to record collector failure for %s", source_name, exc_info=True)


def error_event_security_alerts(*, now=None) -> list[SecurityAlert]:
    """Return active security error event summaries with occurrence counts."""

    del now
    active_events = SecurityAlertEvent.objects.filter(is_active=True)

    alerts: list[SecurityAlert] = []
    for event in active_events.order_by("-last_occurred_at", "-updated_at")[:10]:
        if event.last_occurred_at is None:
            continue
        summary = _(
            "Last seen: %(timestamp)s · Count: %(count)s"
        ) % {
            "timestamp": timezone.localtime(event.last_occurred_at).strftime("%Y-%m-%d %H:%M:%S"),
            "count": event.occurrence_count,
        }
        alerts.append(
            SecurityAlert(
                severity=event.severity,
                message=event.message,
                remediation_url=event.remediation_url,
                summary=str(summary),
            )
        )

    return alerts


def error_event_security_alerts(*, now=None) -> list[SecurityAlert]:
    """Return recent security error event summaries with recency and occurrence counts."""

    current_time = now or timezone.now()
    cutoff = current_time - timedelta(days=14)
    active_events = SecurityAlertEvent.objects.filter(
        is_active=True,
    ).filter(last_occurred_at__gte=cutoff)

    alerts: list[SecurityAlert] = []
    for event in active_events.order_by("-last_occurred_at", "-updated_at")[:10]:
        if event.last_occurred_at is None:
            continue
        summary = _(
            "Last seen: %(timestamp)s · Count: %(count)s"
        ) % {
            "timestamp": timezone.localtime(event.last_occurred_at).strftime("%Y-%m-%d %H:%M:%S"),
            "count": event.occurrence_count,
        }
        alerts.append(
            SecurityAlert(
                severity=event.severity,
                message=event.message,
                remediation_url=event.remediation_url,
                summary=str(summary),
            )
        )

    return alerts


def build_security_alerts() -> list[dict[str, str]]:
    """Aggregate security alerts from all supported data sources."""

    alerts: list[SecurityAlert] = []
    for source_name, collector in (("error_events", error_event_security_alerts),):
        event_key = _COLLECTOR_EVENT_KEYS.get(source_name)
        try:
            alerts.extend(collector())
            if event_key:
                SecurityAlertEvent.clear_occurrence(key=event_key)
        except Exception as exc:
            logger.exception("Security alert source %s failed", source_name)
            try:
                _record_collector_failure_event(source_name=source_name, detail=str(exc))
            except Exception:
                logger.debug("Secondary collector failure recording failed", exc_info=True)

    severity_order = {"error": 0, "warning": 1, "info": 2}
    return [
        {
            "severity": alert.severity,
            "message": alert.message,
            "remediation_url": alert.remediation_url,
            "summary": alert.summary,
        }
        for alert in sorted(
            alerts,
            key=lambda alert: severity_order.get(alert.severity, 99),
        )
    ]
