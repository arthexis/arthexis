"""Charger-independent reporting projection for retained charging sessions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
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

class EnergyProvenance(str, Enum):
    """How normalized session energy entered the reporting contract."""

    RETAINED_NORMALIZED = "retained_normalized"
    UNRESOLVED = "unresolved"

class ReportingCondition(str, Enum):
    """Conditions a downstream report/reconciliation step may need to surface."""

    MISSING_METER_START = "missing_meter_start"
    MISSING_METER_STOP = "missing_meter_stop"
    METER_DISCONTINUITY = "meter_discontinuity"
    UNRESOLVED_ENERGY = "unresolved_energy"
    MISSING_CUSTOMER = "missing_customer"
    MISSING_SITE = "missing_site"
    MISSING_VEHICLE = "missing_vehicle"

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
    authority_node_key: str | None
    authority_node_role: str | None
    connector_number: int | None
    account_key: str | None
    customer_key: str | None
    site_key: str | None
    vehicle_key: str | None
    id_tag: str | None
    started_at: object
    stopped_at: object | None
    meter_start_raw: Decimal | None
    meter_stop_raw: Decimal | None
    energy_kwh: Decimal | None
    energy_provenance: EnergyProvenance
    completeness: SessionCompleteness
    meter_continuity: MeterContinuity
    historical: bool
    source_protocol_transaction_id: str
    source_evidence: tuple[SourceEvidence, ...]
    conditions: tuple[ReportingCondition, ...]

REPORTING_SCHEMA_VERSION = 1
REPORTING_SCHEMA_NAME = "arthexis.charging-session-report"

@dataclass(frozen=True)
class ReportingPeriodProjection:
    """Versioned, transport-safe envelope for a reporting interval."""

    started_at: object
    before: object
    sessions: tuple[ChargingSessionProjection, ...]
    schema_version: int = REPORTING_SCHEMA_VERSION

    @property
    def authority_nodes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    session.authority_node_key
                    for session in self.sessions
                    if session.authority_node_key is not None
                }
            )
        )

    @property
    def total_energy_kwh(self) -> Decimal:
        return sum(
            (
                session.energy_kwh
                for session in self.sessions
                if session.energy_kwh is not None
            ),
            Decimal("0"),
        )

    def _content_dict(self) -> dict[str, object]:
        """Return canonical report content before identity is attached."""

        return {
            "schema": REPORTING_SCHEMA_NAME,
            "schema_version": self.schema_version,
            "started_at": _serialize_scalar(self.started_at),
            "before": _serialize_scalar(self.before),
            "authority_nodes": list(self.authority_nodes),
            "total_energy_kwh": str(self.total_energy_kwh),
            "sessions": [_session_as_dict(session) for session in self.sessions],
        }

    @property
    def content_digest(self) -> str:
        """Identify canonical report content independent of transport/storage."""

        encoded = json.dumps(
            self._content_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible reporting data."""

        return {
            **self._content_dict(),
            "content_digest": self.content_digest,
        }

class ReportingContractError(ValueError):
    """Raised when a serialized reporting payload is incompatible or malformed."""

def reporting_contract() -> dict[str, object]:
    """Describe the stable transport contract without requiring a report run."""

    return {
        "schema": REPORTING_SCHEMA_NAME,
        "schema_version": REPORTING_SCHEMA_VERSION,
        "required_period_fields": (
            "schema",
            "schema_version",
            "started_at",
            "before",
            "authority_nodes",
            "total_energy_kwh",
            "sessions",
            "content_digest",
        ),
        "required_session_fields": tuple(
            field.name for field in fields(ChargingSessionProjection)
        ),
        "enum_values": {
            "energy_provenance": tuple(item.value for item in EnergyProvenance),
            "completeness": tuple(item.value for item in SessionCompleteness),
            "meter_continuity": tuple(item.value for item in MeterContinuity),
            "conditions": tuple(item.value for item in ReportingCondition),
        },
    }

def validate_reporting_payload(payload: object) -> dict[str, object]:
    """Validate one serialized period payload against the current contract."""

    if not isinstance(payload, dict):
        raise ReportingContractError("reporting payload must be an object")

    contract = reporting_contract()
    for field_name in contract["required_period_fields"]:
        if field_name not in payload:
            raise ReportingContractError(
                f"reporting payload is missing required field: {field_name}"
            )

    if payload["schema"] != REPORTING_SCHEMA_NAME:
        raise ReportingContractError(
            f"unsupported reporting schema: {payload['schema']!r}"
        )
    if payload["schema_version"] != REPORTING_SCHEMA_VERSION:
        raise ReportingContractError(
            f"unsupported reporting schema version: {payload['schema_version']!r}"
        )
    digest = payload["content_digest"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ReportingContractError("reporting content_digest must be a sha256 hex digest")

    canonical_content = {
        key: value for key, value in payload.items() if key != "content_digest"
    }
    expected_digest = sha256(
        json.dumps(
            canonical_content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if digest != expected_digest:
        raise ReportingContractError("reporting content_digest does not match payload")

    if not isinstance(payload["sessions"], list):
        raise ReportingContractError("reporting sessions must be a list")
    if not isinstance(payload["authority_nodes"], list):
        raise ReportingContractError("reporting authority_nodes must be a list")

    required_session_fields = contract["required_session_fields"]
    enum_values = contract["enum_values"]
    for index, session in enumerate(payload["sessions"]):
        if not isinstance(session, dict):
            raise ReportingContractError(f"session {index} must be an object")
        for field_name in required_session_fields:
            if field_name not in session:
                raise ReportingContractError(
                    f"session {index} is missing required field: {field_name}"
                )

        for field_name in (
            "energy_provenance",
            "completeness",
            "meter_continuity",
        ):
            if session[field_name] not in enum_values[field_name]:
                raise ReportingContractError(
                    f"session {index} has invalid {field_name}: "
                    f"{session[field_name]!r}"
                )

        conditions = session["conditions"]
        if not isinstance(conditions, list):
            raise ReportingContractError(
                f"session {index} conditions must be a list"
            )
        unknown_conditions = [
            item for item in conditions if item not in enum_values["conditions"]
        ]
        if unknown_conditions:
            raise ReportingContractError(
                f"session {index} has invalid condition: "
                f"{unknown_conditions[0]!r}"
            )

        evidence = session["source_evidence"]
        if not isinstance(evidence, list):
            raise ReportingContractError(
                f"session {index} source_evidence must be a list"
            )
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise ReportingContractError(
                    f"session {index} source_evidence {evidence_index} "
                    "must be an object"
                )
            if not item.get("kind") or not item.get("reference"):
                raise ReportingContractError(
                    f"session {index} source_evidence {evidence_index} "
                    "requires kind and reference"
                )

    return payload

def _serialize_scalar(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

def _session_as_dict(session: ChargingSessionProjection) -> dict[str, object]:
    payload = asdict(session)
    return {
        key: (
            [_source_evidence_as_dict(item) for item in value]
            if key == "source_evidence"
            else [_serialize_scalar(item) for item in value]
            if key == "conditions"
            else _serialize_scalar(value)
        )
        for key, value in payload.items()
    }

def _source_evidence_as_dict(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        return item
    return {
        "kind": getattr(item, "kind"),
        "reference": getattr(item, "reference"),
    }

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
    if hasattr(values, "order_by"):
        values = values.order_by("sampled_at", "pk")
    evidence: list[SourceEvidence] = []
    for value in values:
        fingerprint = getattr(value, "source_fingerprint", "") or ""
        reference = fingerprint or str(getattr(value, "pk", ""))
        if reference:
            evidence.append(SourceEvidence(kind="meter_value", reference=reference))
    return evidence

def _conditions(transaction, *, account_key: str | None) -> tuple[ReportingCondition, ...]:
    conditions: list[ReportingCondition] = []
    if transaction.meter_start is None:
        conditions.append(ReportingCondition.MISSING_METER_START)
    if transaction.meter_stop is None and transaction.stopped_at is not None:
        conditions.append(ReportingCondition.MISSING_METER_STOP)
    if (
        transaction.meter_start is not None
        and transaction.meter_stop is not None
        and transaction.meter_stop < transaction.meter_start
    ):
        conditions.append(ReportingCondition.METER_DISCONTINUITY)
    if transaction.energy_kwh is None and transaction.stopped_at is not None:
        conditions.append(ReportingCondition.UNRESOLVED_ENERGY)
    if account_key is None:
        conditions.append(ReportingCondition.MISSING_CUSTOMER)

    # Site and vehicle are intentionally unresolved until their domain
    # relationships are introduced by the attribution work in #284.
    conditions.extend(
        (ReportingCondition.MISSING_SITE, ReportingCondition.MISSING_VEHICLE)
    )
    return tuple(conditions)

def projected_sessions_for_period(queryset, *, started_at, before):
    """Yield stable projections for sessions starting inside one reporting period."""

    selected = (
        queryset.filter(started_at__gte=started_at, started_at__lt=before)
        .select_related("charger", "charger__node", "connector", "account")
        .prefetch_related("meter_values")
        .order_by("started_at", "pk")
    )
    for transaction in selected:
        yield project_charging_session(transaction)

def project_reporting_period(queryset, *, started_at, before) -> ReportingPeriodProjection:
    """Materialize one versioned reporting-period envelope."""

    return ReportingPeriodProjection(
        started_at=started_at,
        before=before,
        sessions=tuple(
            projected_sessions_for_period(
                queryset,
                started_at=started_at,
                before=before,
            )
        ),
    )

def project_charging_session(transaction) -> ChargingSessionProjection:
    """Project one retained OCPP transaction into the stable reporting contract."""

    charger = transaction.charger
    node = getattr(charger, "node", None)
    connector = getattr(transaction, "connector", None)
    account = getattr(transaction, "account", None)
    account_key = getattr(account, "key", None)
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
        authority_node_key=getattr(node, "identifier", None),
        authority_node_role=getattr(node, "role", None),
        connector_number=getattr(connector, "number", None),
        account_key=account_key,
        customer_key=account_key,
        site_key=None,
        vehicle_key=None,
        id_tag=transaction.id_tag or None,
        started_at=transaction.started_at,
        stopped_at=transaction.stopped_at,
        meter_start_raw=transaction.meter_start,
        meter_stop_raw=transaction.meter_stop,
        energy_kwh=transaction.energy_kwh,
        energy_provenance=(
            EnergyProvenance.RETAINED_NORMALIZED
            if transaction.energy_kwh is not None
            else EnergyProvenance.UNRESOLVED
        ),
        completeness=_completeness(transaction),
        meter_continuity=_meter_continuity(
            transaction.meter_start,
            transaction.meter_stop,
        ),
        historical=bool(transaction.historical),
        source_protocol_transaction_id=remote_id,
        source_evidence=tuple(evidence),
        conditions=_conditions(transaction, account_key=account_key),
    )
