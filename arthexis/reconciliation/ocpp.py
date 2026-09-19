"""Secondary OCPP reconciliation records kept apart from core identities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.utils.timezone import now

if TYPE_CHECKING:
    from arthexis.reconciliation.importer import _Importer


def _value(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    return next(
        (row[name] for name in names if row.get(name) not in (None, "")), default
    )


def import_ocpp_records(importer: _Importer) -> None:
    """Import retained non-session OCPP state without legacy payload blobs."""
    from apps.ocpp.models import (
        CertificateRecord,
        ChargerVariable,
        ChargingProfile,
        MonitoringRecord,
        NotificationRecord,
        OperationalStatusRecord,
        Reservation,
    )

    for row in importer._rows("charging_profiles", "ocpp_chargingprofile"):
        charger = importer.chargers.get(_value(row, "charger_id", "charge_point_id"))
        remote_id = str(_value(row, "charging_profile_id", "remote_id", "id"))[:80]
        if charger and remote_id:
            ChargingProfile.objects.update_or_create(
                charger=charger,
                remote_id=remote_id,
                defaults={
                    "stack_level": int(_value(row, "stack_level", default=0)),
                    "purpose": str(_value(row, "purpose", default="TxProfile"))[:60],
                    "kind": str(_value(row, "kind", default="Absolute"))[:60],
                    "active": True,
                },
            )
            importer.report.count("charging_profiles")
    for row in importer._rows("reservations", "ocpp_cpreservation"):
        charger_id = _value(row, "charger_id", "charge_point_id")
        charger = importer.chargers.get(charger_id)
        remote_id = str(_value(row, "reservation_id", "remote_id", "id"))[:80]
        if charger and remote_id:
            Reservation.objects.update_or_create(
                remote_id=remote_id,
                defaults={
                    "charger": charger,
                    "connector": importer.connectors.get(
                        (charger_id, _value(row, "connector_id"))
                    ),
                    "id_tag": str(_value(row, "id_tag", "idTag", default="legacy"))[
                        :40
                    ],
                    "expires_at": _value(
                        row, "expiry_date", "expires_at", default=now()
                    ),
                    "status": str(_value(row, "status", default="pending"))[:30],
                },
            )
            importer.report.count("reservations")
    for row in importer._rows("charger_variables", "ocpp_chargervariable"):
        charger = importer.chargers.get(_value(row, "charger_id", "charge_point_id"))
        if charger:
            ChargerVariable.objects.update_or_create(
                charger=charger,
                component=str(_value(row, "component", default="Legacy"))[:120],
                variable=str(_value(row, "variable", "key", default="unknown"))[:120],
                attribute_type=str(_value(row, "attribute_type", default="Actual"))[
                    :30
                ],
                defaults={
                    "value": str(_value(row, "value", default="")),
                    "mutable": False,
                },
            )
            importer.report.count("charger_variables")
    for row in importer._rows("certificate_metadata", "certs_certificatebase"):
        charger = importer.chargers.get(_value(row, "charger_id", "charge_point_id"))
        fingerprint = str(_value(row, "fingerprint", "certificate_hash", default=""))
        if charger and fingerprint:
            CertificateRecord.objects.update_or_create(
                fingerprint=fingerprint[:128],
                defaults={
                    "charger": charger,
                    "certificate_type": str(
                        _value(row, "certificate_type", default="legacy")
                    )[:60],
                    "status": str(_value(row, "status", default="accepted"))[:40],
                },
            )
            importer.report.count("certificate_metadata")
    _import_events(
        importer, NotificationRecord, MonitoringRecord, OperationalStatusRecord
    )


def _import_events(
    importer: _Importer, notifications: object, monitoring: object, statuses: object
) -> None:
    for row in importer._rows("notifications", "ocpp_notificationrecord"):
        charger = importer.chargers.get(_value(row, "charger_id", "charge_point_id"))
        if charger:
            notifications.objects.get_or_create(
                charger=charger,
                action=str(_value(row, "action", "event_type", default="legacy"))[:80],
                received_at=_value(row, "received_at", "created_at", default=now()),
                defaults={"payload": {}},
            )
            importer.report.count("notifications")
    for row in importer._rows("monitoring", "ocpp_monitoringrecord"):
        charger = importer.chargers.get(_value(row, "charger_id", "charge_point_id"))
        if charger:
            monitoring.objects.get_or_create(
                charger=charger,
                event_type=str(_value(row, "event_type", default="legacy"))[:80],
                occurred_at=_value(row, "occurred_at", "created_at", default=now()),
                defaults={"payload": {}},
            )
            importer.report.count("monitoring")
    for row in importer._rows("operational_status", "ocpp_operationalstatusrecord"):
        charger = importer.chargers.get(_value(row, "charger_id", "charge_point_id"))
        kind = str(_value(row, "kind", default="firmware"))
        if charger and kind in {"diagnostics", "firmware", "log"}:
            statuses.objects.get_or_create(
                charger=charger,
                kind=kind,
                status=str(_value(row, "status", default="legacy"))[:80],
                occurred_at=_value(row, "occurred_at", "created_at", default=now()),
                defaults={"payload": {}},
            )
            importer.report.count("operational_status")
