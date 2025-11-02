from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Sequence

try:  # pragma: no cover - optional dependency
    import graphene
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    graphene = None

try:  # pragma: no cover - optional dependency
    from graphql import GraphQLError as _GraphQLError
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    class GraphQLError(Exception):
        """Fallback GraphQL error used when ``graphql-core`` is unavailable."""

        def __init__(self, message: str):
            super().__init__(message)
            self.message = message

        def __str__(self) -> str:  # pragma: no cover - simple representation
            return self.message

    _GraphQLError = GraphQLError
else:  # pragma: no cover - import path
    GraphQLError = _GraphQLError

from django.contrib.auth.models import AnonymousUser
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ocpp.models import MeterValue, Transaction

REQUIRED_PERMISSIONS = (
    "ocpp.view_transaction",
    "ocpp.view_metervalue",
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class EnergySessionOrder(Enum):
    NEWEST_FIRST = "desc"
    OLDEST_FIRST = "asc"


@dataclass(slots=True)
class MeterReadingRecord:
    timestamp: datetime
    context: str | None
    connector_id: int | None
    energy_kwh: float | None
    voltage: float | None
    current_import: float | None
    temperature: float | None
    soc: float | None


@dataclass(slots=True)
class EnergySessionRecord:
    id: str
    charger_id: str
    connector_id: int | None
    account: str | None
    started_at: datetime
    stopped_at: datetime | None
    energy_kwh: float
    meter_values: Sequence[MeterReadingRecord]


@dataclass(slots=True)
class EnergySessionEdgeRecord:
    cursor: str
    node: EnergySessionRecord


@dataclass(slots=True)
class PageInfoRecord:
    has_next_page: bool
    end_cursor: str | None


@dataclass(slots=True)
class EnergySessionConnectionRecord:
    total_count: int
    edges: Sequence[EnergySessionEdgeRecord]
    page_info: PageInfoRecord


@dataclass(slots=True)
class EnergyExportStatusRecord:
    ready: bool
    message: str


@dataclass(slots=True)
class ExecutionResult:
    data: dict | None
    errors: list[dict] | None


def _assert_has_permissions(user) -> None:
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        raise GraphQLError("Authentication required.")

    missing = [perm for perm in REQUIRED_PERMISSIONS if not user.has_perm(perm)]
    if missing:
        raise GraphQLError("You do not have permission to access energy export data.")


def _resolve_energy_export_status(user) -> EnergyExportStatusRecord:
    _assert_has_permissions(user)
    return EnergyExportStatusRecord(
        ready=True, message="Energy export GraphQL endpoint is online."
    )


def _resolve_energy_sessions(user, filters: dict | None, pagination: dict | None) -> EnergySessionConnectionRecord:
    _assert_has_permissions(user)

    filters = filters or {}
    pagination = pagination or {}

    order = pagination.get("order")
    if order is None:
        order = EnergySessionOrder.NEWEST_FIRST.value
    else:
        order = _normalize_order(order)

    first = pagination.get("first") or DEFAULT_PAGE_SIZE
    try:
        first = int(first)
    except (TypeError, ValueError):
        raise GraphQLError("Pagination 'first' must be an integer.")
    if first < 1:
        raise GraphQLError("Pagination 'first' must be at least 1.")
    if first > MAX_PAGE_SIZE:
        first = MAX_PAGE_SIZE

    cursor = pagination.get("after")

    start_time = filters.get("start_time")
    stop_time = filters.get("stop_time")
    if start_time and stop_time and start_time > stop_time:
        raise GraphQLError("'startTime' must be before 'stopTime'.")

    qs = _build_energy_session_queryset()
    qs = _apply_session_filters(qs, filters)
    total_count = qs.count()
    qs = _apply_ordering(qs, order)
    qs = _apply_cursor(qs, cursor, order)

    results = list(qs[: first + 1])
    has_next_page = len(results) > first
    results = results[:first]

    edges = [
        EnergySessionEdgeRecord(cursor=_encode_cursor(order, tx), node=_serialize_transaction(tx))
        for tx in results
    ]

    end_cursor = edges[-1].cursor if edges else None
    return EnergySessionConnectionRecord(
        total_count=total_count,
        edges=edges,
        page_info=PageInfoRecord(has_next_page=has_next_page, end_cursor=end_cursor),
    )


def _build_energy_session_queryset():
    return (
        Transaction.objects.filter(charger__isnull=False)
        .select_related("charger", "charger__location", "account")
        .prefetch_related(
            Prefetch("meter_values", queryset=MeterValue.objects.order_by("timestamp"))
        )
    )


def _apply_session_filters(qs, filters):
    charger_ids = filters.get("charger_ids") or []
    if charger_ids:
        qs = qs.filter(charger__charger_id__in=charger_ids)

    account_ids = []
    for value in filters.get("account_ids") or []:
        value_str = str(value).strip()
        if value_str.isdigit():
            account_ids.append(int(value_str))
    if account_ids:
        qs = qs.filter(account__pk__in=account_ids)

    location_ids = []
    for value in filters.get("location_ids") or []:
        value_str = str(value).strip()
        if value_str.isdigit():
            location_ids.append(int(value_str))
    if location_ids:
        qs = qs.filter(charger__location__pk__in=location_ids)

    start_time = filters.get("start_time")
    if start_time:
        qs = qs.filter(start_time__gte=start_time)

    stop_time = filters.get("stop_time")
    if stop_time:
        qs = qs.filter(start_time__lte=stop_time)

    return qs


def _apply_ordering(qs, order: str):
    if order == EnergySessionOrder.OLDEST_FIRST.value:
        return qs.order_by("start_time", "pk")
    return qs.order_by("-start_time", "-pk")


def _apply_cursor(qs, cursor: str | None, order: str):
    if not cursor:
        return qs

    cursor_order, cursor_time, cursor_pk = _decode_cursor(cursor)
    if cursor_order != order:
        raise GraphQLError("Pagination cursor does not match requested order.")

    if order == EnergySessionOrder.OLDEST_FIRST.value:
        return qs.filter(
            Q(start_time__gt=cursor_time)
            | (Q(start_time=cursor_time) & Q(pk__gt=cursor_pk))
        )

    return qs.filter(
        Q(start_time__lt=cursor_time)
        | (Q(start_time=cursor_time) & Q(pk__lt=cursor_pk))
    )


def _decode_cursor(cursor: str) -> tuple[str, datetime, int]:
    try:
        raw = base64.b64decode(cursor.encode("ascii")).decode("utf-8")
        cursor_order, dt_value, pk_value = raw.split("|", 2)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise GraphQLError("Invalid pagination cursor.") from exc

    dt = parse_datetime(dt_value)
    if dt is None:
        raise GraphQLError("Invalid pagination cursor timestamp.")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())

    try:
        pk = int(pk_value)
    except (TypeError, ValueError) as exc:
        raise GraphQLError("Invalid pagination cursor id.") from exc

    if cursor_order not in {
        EnergySessionOrder.NEWEST_FIRST.value,
        EnergySessionOrder.OLDEST_FIRST.value,
    }:
        raise GraphQLError("Invalid pagination cursor order.")

    return cursor_order, dt, pk


def _encode_cursor(order: str, tx: Transaction) -> str:
    raw = f"{order}|{tx.start_time.isoformat()}|{tx.pk}"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _serialize_transaction(tx: Transaction) -> EnergySessionRecord:
    meter_values = list(tx.meter_values.all())
    readings = [_serialize_meter_value(mv) for mv in meter_values]
    account_name = tx.account.name if tx.account else None
    connector_id = tx.connector_id or (tx.charger.connector_id if tx.charger else None)

    return EnergySessionRecord(
        id=str(tx.pk),
        charger_id=tx.charger.charger_id,
        connector_id=connector_id,
        account=account_name,
        started_at=tx.start_time,
        stopped_at=tx.stop_time,
        energy_kwh=_compute_energy_kwh(tx, meter_values),
        meter_values=readings,
    )


def _serialize_meter_value(mv: MeterValue) -> MeterReadingRecord:
    def _as_float(value):
        return float(value) if value is not None else None

    return MeterReadingRecord(
        timestamp=mv.timestamp,
        context=mv.context or None,
        connector_id=mv.connector_id,
        energy_kwh=_as_float(mv.energy),
        voltage=_as_float(mv.voltage),
        current_import=_as_float(mv.current_import),
        temperature=_as_float(mv.temperature),
        soc=_as_float(mv.soc),
    )


def _compute_energy_kwh(tx: Transaction, meter_values: Iterable[MeterValue]) -> float:
    start_val = float(tx.meter_start) / 1000.0 if tx.meter_start is not None else None
    end_val = float(tx.meter_stop) / 1000.0 if tx.meter_stop is not None else None

    readings = [mv for mv in meter_values if mv.energy is not None]
    if readings:
        readings.sort(key=lambda mv: mv.timestamp)
        if start_val is None:
            start_val = float(readings[0].energy or 0)
        if end_val is None:
            end_val = float(readings[-1].energy or 0)

    if start_val is None or end_val is None:
        return 0.0

    total = end_val - start_val
    return max(total, 0.0)


def _normalize_order(order) -> str:
    if isinstance(order, EnergySessionOrder):
        return order.value

    if graphene and isinstance(order, graphene.EnumMeta):  # pragma: no cover - defensive
        order = order.value

    if hasattr(order, "value"):
        order_value = getattr(order, "value")
        if isinstance(order_value, str):
            if order_value in {"asc", "desc"}:
                return order_value
            try:
                return EnergySessionOrder[order_value].value
            except KeyError as exc:
                raise GraphQLError("Invalid pagination order value.") from exc

    if isinstance(order, str):
        order_str = order.strip()
        if not order_str:
            return EnergySessionOrder.NEWEST_FIRST.value
        if order_str in {"asc", "desc"}:
            return order_str
        try:
            return EnergySessionOrder[order_str].value
        except KeyError as exc:
            raise GraphQLError("Invalid pagination order value.") from exc

    raise GraphQLError("Invalid pagination order value.")


def _prepare_filters_for_fallback(raw: dict | None) -> dict:
    if not raw:
        return {}

    mapping = {
        "chargerIds": "charger_ids",
        "accountIds": "account_ids",
        "locationIds": "location_ids",
        "startTime": "start_time",
        "stopTime": "stop_time",
    }

    prepared: dict = {}
    for key, value in raw.items():
        target = mapping.get(key, key)
        if target in {"start_time", "stop_time"}:
            prepared[target] = _parse_datetime_input(value)
        else:
            prepared[target] = value
    return prepared


def _prepare_pagination_for_fallback(raw: dict | None) -> dict:
    if not raw:
        return {}

    prepared = dict(raw)
    if "order" in prepared and prepared["order"] is not None:
        prepared["order"] = _normalize_order(prepared["order"])
    return prepared


def _parse_datetime_input(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if not timezone.is_naive(value) else timezone.make_aware(value)
    if isinstance(value, str):
        dt = parse_datetime(value)
        if dt is None:
            raise GraphQLError("Invalid datetime value provided.")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return dt
    raise GraphQLError("Invalid datetime value provided.")


def _connection_to_dict(connection: EnergySessionConnectionRecord) -> dict:
    return {
        "totalCount": connection.total_count,
        "pageInfo": {
            "hasNextPage": connection.page_info.has_next_page,
            "endCursor": connection.page_info.end_cursor,
        },
        "edges": [
            {
                "cursor": edge.cursor,
                "node": _session_to_dict(edge.node),
            }
            for edge in connection.edges
        ],
    }


def _session_to_dict(session: EnergySessionRecord) -> dict:
    return {
        "id": session.id,
        "chargerId": session.charger_id,
        "connectorId": session.connector_id,
        "account": session.account,
        "startedAt": session.started_at.isoformat(),
        "stoppedAt": session.stopped_at.isoformat() if session.stopped_at else None,
        "energyKwh": session.energy_kwh,
        "meterValues": [_meter_reading_to_dict(mv) for mv in session.meter_values],
    }


def _meter_reading_to_dict(reading: MeterReadingRecord) -> dict:
    return {
        "timestamp": reading.timestamp.isoformat(),
        "context": reading.context,
        "connectorId": reading.connector_id,
        "energyKwh": reading.energy_kwh,
        "voltage": reading.voltage,
        "currentImport": reading.current_import,
        "temperature": reading.temperature,
        "soc": reading.soc,
    }


def _execute_fallback(query: str, variables: dict | None, context) -> ExecutionResult:
    variables = variables or {}
    data: dict = {}

    try:
        user = getattr(context, "user", None)

        lowered = query or ""
        if "energyExportStatus" in lowered:
            status = _resolve_energy_export_status(user)
            data["energyExportStatus"] = {
                "ready": status.ready,
                "message": status.message,
            }

        if "energySessions" in lowered:
            if "filter" in variables:
                filters = _prepare_filters_for_fallback(variables.get("filter"))
            else:
                raise GraphQLError(
                    'Argument "filter" of required type "EnergySessionFilterInput!" was not provided.'
                )
            pagination = _prepare_pagination_for_fallback(variables.get("pagination"))
            connection = _resolve_energy_sessions(user, filters, pagination)
            data["energySessions"] = _connection_to_dict(connection)
    except GraphQLError as exc:
        return ExecutionResult(data=None, errors=[{"message": str(exc)}])

    return ExecutionResult(data=data or {}, errors=None)


class FallbackSchema:
    """Minimal schema interface used when ``graphene`` is unavailable."""

    def execute(self, query: str, variable_values=None, context_value=None):
        return _execute_fallback(query, variable_values, context_value)


if graphene:
    GrapheneEnergySessionOrder = graphene.Enum.from_enum(EnergySessionOrder)

    class EnergyExportStatus(graphene.ObjectType):
        ready = graphene.Boolean(
            required=True, description="Whether the endpoint is accepting requests."
        )
        message = graphene.String(
            required=True, description="Human readable status description."
        )

    class MeterReading(graphene.ObjectType):
        timestamp = graphene.DateTime(required=True)
        context = graphene.String()
        connector_id = graphene.Int(name="connectorId")
        energy_kwh = graphene.Float(name="energyKwh")
        voltage = graphene.Float()
        current_import = graphene.Float(name="currentImport")
        temperature = graphene.Float()
        soc = graphene.Float()

    class EnergySession(graphene.ObjectType):
        id = graphene.ID(required=True)
        charger_id = graphene.String(required=True, name="chargerId")
        connector_id = graphene.Int(name="connectorId")
        account = graphene.String()
        started_at = graphene.DateTime(required=True, name="startedAt")
        stopped_at = graphene.DateTime(name="stoppedAt")
        energy_kwh = graphene.Float(required=True, name="energyKwh")
        meter_values = graphene.List(MeterReading, required=True, name="meterValues")

        def resolve_meter_values(self, info):
            return self.meter_values

    class EnergySessionEdge(graphene.ObjectType):
        cursor = graphene.String(required=True)
        node = graphene.Field(EnergySession, required=True)

        def resolve_node(self, info):
            return self.node

    class PageInfo(graphene.ObjectType):
        has_next_page = graphene.Boolean(required=True, name="hasNextPage")
        end_cursor = graphene.String(name="endCursor")

    class EnergySessionConnection(graphene.ObjectType):
        total_count = graphene.Int(required=True, name="totalCount")
        edges = graphene.List(EnergySessionEdge, required=True)
        page_info = graphene.Field(PageInfo, required=True, name="pageInfo")

        def resolve_edges(self, info):
            return self.edges

        def resolve_page_info(self, info):
            return self.page_info

    class PaginationInput(graphene.InputObjectType):
        first = graphene.Int()
        after = graphene.String()
        order = GrapheneEnergySessionOrder()

    class EnergySessionFilterInput(graphene.InputObjectType):
        charger_ids = graphene.List(graphene.String, name="chargerIds")
        account_ids = graphene.List(graphene.ID, name="accountIds")
        location_ids = graphene.List(graphene.ID, name="locationIds")
        start_time = graphene.DateTime(name="startTime")
        stop_time = graphene.DateTime(name="stopTime")

    class Query(graphene.ObjectType):
        energy_export_status = graphene.Field(
            EnergyExportStatus,
            description="Return readiness information for the energy export GraphQL endpoint.",
        )
        energy_sessions = graphene.Field(
            EnergySessionConnection,
            filter=graphene.Argument(
                EnergySessionFilterInput,
                required=True,
                description="Filtering criteria for selecting energy sessions.",
            ),
            pagination=graphene.Argument(
                PaginationInput,
                required=False,
                description="Pagination options for the session list.",
            ),
            description="Return transaction-backed energy sessions with meter readings.",
        )

        @staticmethod
        def resolve_energy_export_status(root, info):
            status = _resolve_energy_export_status(getattr(info.context, "user", None))
            return EnergyExportStatus(ready=status.ready, message=status.message)

        @staticmethod
        def resolve_energy_sessions(root, info, filter, pagination=None):
            connection = _resolve_energy_sessions(
                getattr(info.context, "user", None), filter, pagination
            )
            return connection

    schema = graphene.Schema(query=Query)
else:
    schema = FallbackSchema()

