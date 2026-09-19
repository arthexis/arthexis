"""Notification, monitoring, certificate, and status persistence services."""

from datetime import datetime

from apps.ocpp.models import (
    CertificateRecord,
    Charger,
    MonitoringRecord,
    NotificationRecord,
    OperationalStatusRecord,
)


def record_notification(
    *, charger: Charger, action: str, payload: dict[str, object]
) -> NotificationRecord:
    """Persist one retained OCPP notification payload."""
    return NotificationRecord.objects.create(
        charger=charger,
        action=action,
        payload=payload,
    )


def record_monitoring(
    *,
    charger: Charger,
    event_type: str,
    payload: dict[str, object],
    component: str = "",
    variable: str = "",
    severity: int | None = None,
) -> MonitoringRecord:
    """Persist one monitoring or report record."""
    return MonitoringRecord.objects.create(
        charger=charger,
        component=component,
        variable=variable,
        severity=severity,
        event_type=event_type,
        payload=payload,
    )


def record_certificate(
    *,
    fingerprint: str,
    certificate_type: str,
    charger: Charger | None = None,
    expires_at: datetime | None = None,
    status: str = "accepted",
) -> CertificateRecord:
    """Upsert certificate metadata without writing certificate material."""
    record, _ = CertificateRecord.objects.update_or_create(
        fingerprint=fingerprint,
        defaults={
            "charger": charger,
            "certificate_type": certificate_type,
            "expires_at": expires_at,
            "status": status,
        },
    )
    return record


def record_operational_status(
    *,
    charger: Charger,
    kind: str,
    status: str,
    payload: dict[str, object],
) -> OperationalStatusRecord:
    """Persist a firmware, diagnostics, or log status notification."""
    return OperationalStatusRecord.objects.create(
        charger=charger,
        kind=kind,
        status=status,
        payload=payload,
    )
