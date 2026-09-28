"""Charger-independent reporting projection for retained charging sessions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SessionCompleteness(str, Enum):
    """Reporting completeness independent of charger/protocol implementation."""

    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class MeterContinuity(str, Enum):
    """Whether retained meter boundaries can be treated as one continuous counter."""

    CONTINUOUS = "continuous"
    DISCONTINUOUS = "discontinuous"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceEvidence:
    """Stable reference back to retained source evidence."""

    kind: str
    reference: str


@dataclass(frozen=True)
class ChargingSessionProjection:
    """Read-only normalized reporting shape derived from retained domain records."""

    session_id: str
    charger_identity: str
    connector_number: int | None
    account_key: str | None
    site_key: str | None
    vehicle_key: str | None
    id_tag: str | None
    started_at: object
    stopped_at: object | None
    meter_start_raw: Decimal | None
    meter_stop_raw: Decimal | None
    energy_kwh: Decimal | None
    completeness: SessionCompleteness
    meter_continuity: MeterContinuity
    historical: bool
    source_protocol_transaction_id: str
    source_evidence: tuple[SourceEvidence, ...]


def _meter_continuity(meter_start: Decimal | None, meter_stop: Decimal | None) -> MeterContinuity:
    if meter_start is None or meter_stop is None:
        return MeterContinuity.UNKNOWN
    if meter_stop < meter_start:
        return MeterContinuity.DISCONTINUOUS
    return MeterContinuity.CONTINUOUS


def _completeness(transaction) -> SessionCompleteness:
    if transaction.stopped_at is None:
        return SessionCompleteness.IN_PROGRESS
    if transaction.energy_kwh is None:
        return SessionCompleteness.INCOMPLETE
    return SessionCompleteness.COMPLETE


def _meter_evidence(transaction) -> Iterable[SourceEvidence]:
    manager = getattr(transaction, "meter_values", None)
    if manager is None:
        return ()
    values = manager.all() if hasattr(manager, "all") else manager
    evidence: list[SourceEvidence] = []
    for value in values:
        fingerprint = getattr(value, "source_fingerprint", "") or ""
        reference = fingerprint or str(getattr(value, "pk", ""))
        if reference:
            evidence.append(SourceEvidence(kind="meter_value", reference=reference))
    return evidence


def project_charging_session(transaction) -> ChargingSessionProjection:
    """Project one retained OCPP transaction into the stable reporting contract."""

    charger = transaction.charger
    connector = getattr(transaction, "connector", None)
    account = getattr(transaction, "account", None)
    remote_id = str(transaction.remote_id)

    evidence = [
        SourceEvidence(
            kind="ocpp_transaction",
            reference=remote_id or str(getattr(transaction, "pk", "")),
        )
    ]
    evidence.extend(_meter_evidence(transaction))

    return ChargingSessionProjection(
        session_id=f"{charger.identity}:{remote_id}",
        charger_identity=charger.identity,
        connector_number=getattr(connector, "number", None),
        account_key=getattr(account, "key", None),
        site_key=None,
        vehicle_key=None,
        id_tag=transaction.id_tag or None,
        started_at=transaction.started_at,
        stopped_at=transaction.stopped_at,
        meter_start_raw=transaction.meter_start,
        meter_stop_raw=transaction.meter_stop,
        energy_kwh=transaction.energy_kwh,
        completeness=_completeness(transaction),
        meter_continuity=_meter_continuity(
            transaction.meter_start,
            transaction.meter_stop,
        ),
        historical=bool(transaction.historical),
        source_protocol_transaction_id=remote_id,
        source_evidence=tuple(evidence),
    )
