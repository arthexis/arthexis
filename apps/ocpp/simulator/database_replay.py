"""Read-only replay sources backed by migrated Arthexis databases."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.simulator.client import OcppSimulator


class ReplayTransport(Protocol):
    """Minimal transport used by replay execution."""

    def call(
        self,
        action: str,
        payload: dict[str, object],
    ) -> Awaitable[dict[str, object]]: ...


class ReconnectableReplayTransport(ReplayTransport, Protocol):
    """Replay transport that can cycle its live connection and boot again."""

    def reconnect(self) -> Awaitable[None]: ...

    def boot(self) -> Awaitable[object]: ...


@dataclass(frozen=True)
class ReplayPacing:
    """Control replay timing independently from historical timestamps."""

    mode: str = "maximum"
    interval_seconds: float = 0.0
    burst_size: int = 100
    burst_pause_seconds: float = 0.0

    def validate(self) -> None:
        if self.mode not in {"maximum", "fixed", "burst"}:
            raise ValueError("Replay pacing mode must be maximum, fixed, or burst.")
        if self.interval_seconds < 0 or self.burst_pause_seconds < 0:
            raise ValueError("Replay pacing delays cannot be negative.")
        if self.burst_size < 1:
            raise ValueError("Replay burst size must be at least 1.")


@dataclass
class ReplayMetrics:
    """Bounded aggregate measurements for one replay run."""

    attempted_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    transport_failures: int = 0
    reconnect_attempts: int = 0
    reconnect_successes: int = 0
    reconnect_failures: int = 0
    total_latency_seconds: float = 0.0
    max_latency_seconds: float = 0.0
    total_reconnect_seconds: float = 0.0
    max_reconnect_seconds: float = 0.0
    error_counts: dict[str, int] = field(default_factory=dict)
    _started_at: float = field(default_factory=time.monotonic, repr=False)
    elapsed_seconds: float = 0.0

    def start_request(self) -> float:
        self.attempted_requests += 1
        return time.monotonic()

    def record_success(self, started_at: float) -> None:
        latency = time.monotonic() - started_at
        self.completed_requests += 1
        self.total_latency_seconds += latency
        self.max_latency_seconds = max(self.max_latency_seconds, latency)

    def record_failure(self, started_at: float, exc: Exception) -> None:
        latency = time.monotonic() - started_at
        self.failed_requests += 1
        self.transport_failures += 1
        self.total_latency_seconds += latency
        self.max_latency_seconds = max(self.max_latency_seconds, latency)
        name = type(exc).__name__
        self.error_counts[name] = self.error_counts.get(name, 0) + 1

    def start_reconnect(self) -> float:
        self.reconnect_attempts += 1
        return time.monotonic()

    def record_reconnect_success(self, started_at: float) -> None:
        elapsed = time.monotonic() - started_at
        self.reconnect_successes += 1
        self.total_reconnect_seconds += elapsed
        self.max_reconnect_seconds = max(self.max_reconnect_seconds, elapsed)

    def record_reconnect_failure(self, started_at: float, exc: Exception) -> None:
        elapsed = time.monotonic() - started_at
        self.reconnect_failures += 1
        self.total_reconnect_seconds += elapsed
        self.max_reconnect_seconds = max(self.max_reconnect_seconds, elapsed)
        name = type(exc).__name__
        self.error_counts[name] = self.error_counts.get(name, 0) + 1

    def finish(self) -> None:
        self.elapsed_seconds = max(0.0, time.monotonic() - self._started_at)

    def as_dict(self) -> dict[str, object]:
        measured = self.completed_requests + self.failed_requests
        mean_latency = self.total_latency_seconds / measured if measured else 0.0
        throughput = (
            self.completed_requests / self.elapsed_seconds
            if self.elapsed_seconds > 0
            else 0.0
        )
        mean_reconnect = (
            self.total_reconnect_seconds / self.reconnect_attempts
            if self.reconnect_attempts
            else 0.0
        )
        return {
            "attempted_requests": self.attempted_requests,
            "completed_requests": self.completed_requests,
            "failed_requests": self.failed_requests,
            "transport_failures": self.transport_failures,
            "reconnect_attempts": self.reconnect_attempts,
            "reconnect_successes": self.reconnect_successes,
            "reconnect_failures": self.reconnect_failures,
            "elapsed_seconds": self.elapsed_seconds,
            "throughput_requests_per_second": throughput,
            "mean_latency_seconds": mean_latency,
            "max_latency_seconds": self.max_latency_seconds,
            "mean_reconnect_seconds": mean_reconnect,
            "max_reconnect_seconds": self.max_reconnect_seconds,
            "error_counts": dict(sorted(self.error_counts.items())),
        }


@dataclass(frozen=True)
class ReplayEvent:
    """One charger-originated event derived from retained historical evidence."""

    source_transaction_id: int
    occurred_at: str
    action: str
    payload: dict[str, object]
    requires_runtime_transaction_id: bool = False


def _connect(database: Path) -> sqlite3.Connection:
    path = database.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Replay database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


async def _pace(pacing: ReplayPacing, sent: int) -> None:
    pacing.validate()
    if pacing.mode == "maximum":
        return
    if pacing.mode == "fixed":
        if sent:
            await asyncio.sleep(pacing.interval_seconds)
        return
    if sent and sent % pacing.burst_size == 0:
        await asyncio.sleep(pacing.burst_pause_seconds)


def _require_current_generation(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    required = {
        "base_schemageneration",
        "ocpp_charger",
        "ocpp_ocpptransaction",
        "ocpp_metervalue",
    }
    missing = required - tables
    if missing:
        raise ValueError(
            "Replay requires a migrated/current-generation Arthexis database; "
            f"missing tables: {', '.join(sorted(missing))}"
        )
    generation = connection.execute(
        "SELECT 1 FROM base_schemageneration WHERE generation = 2 LIMIT 1"
    ).fetchone()
    if generation is None:
        raise ValueError(
            "Replay requires a migrated/current-generation Arthexis database."
        )


def iter_v16_transaction_replay(
    database: Path,
    *,
    charger_identity: str | None = None,
    batch_size: int = 250,
) -> Iterator[ReplayEvent]:
    """Stream historical OCPP 1.6 transaction evidence in deterministic order."""

    if batch_size < 1:
        raise ValueError("Replay batch size must be at least 1.")

    with _connect(database) as connection:
        _require_current_generation(connection)

        parameters: list[object] = []
        where = ""
        if charger_identity is not None:
            where = "WHERE charger.identity = ?"
            parameters.append(charger_identity)

        transactions = connection.execute(
            f"""
            SELECT
                tx.id,
                tx.id_tag,
                tx.started_at,
                tx.stopped_at,
                tx.meter_start,
                tx.meter_stop,
                charger.identity AS charger_identity,
                connector.number AS connector_number
            FROM ocpp_ocpptransaction AS tx
            JOIN ocpp_charger AS charger ON charger.id = tx.charger_id
            LEFT JOIN ocpp_connector AS connector ON connector.id = tx.connector_id
            {where}
            ORDER BY tx.started_at, tx.id
            """,
            parameters,
        )

        while True:
            rows = transactions.fetchmany(batch_size)
            if not rows:
                break
            for transaction in rows:
                transaction_id = int(transaction["id"])
                connector_number = transaction["connector_number"] or 1
                started_at = str(transaction["started_at"])
                start_payload: dict[str, object] = {
                    "connectorId": int(connector_number),
                    "idTag": str(transaction["id_tag"] or ""),
                    "meterStart": _integer_meter(transaction["meter_start"]),
                    "timestamp": started_at,
                }
                yield ReplayEvent(
                    source_transaction_id=transaction_id,
                    occurred_at=started_at,
                    action="StartTransaction",
                    payload=start_payload,
                )

                yield from _meter_events(connection, transaction_id)

                stopped_at = transaction["stopped_at"]
                if stopped_at is not None:
                    yield ReplayEvent(
                        source_transaction_id=transaction_id,
                        occurred_at=str(stopped_at),
                        action="StopTransaction",
                        payload={
                            "meterStop": _integer_meter(transaction["meter_stop"]),
                            "timestamp": str(stopped_at),
                        },
                        requires_runtime_transaction_id=True,
                    )


def _meter_events(
    connection: sqlite3.Connection,
    source_transaction_id: int,
) -> Iterator[ReplayEvent]:
    cursor = connection.execute(
        """
        SELECT sampled_at, value, measurand, unit, multiplier, id
        FROM ocpp_metervalue
        WHERE transaction_id = ?
        ORDER BY sampled_at, id
        """,
        (source_transaction_id,),
    )
    for row in cursor:
        sampled_at = str(row["sampled_at"])
        yield ReplayEvent(
            source_transaction_id=source_transaction_id,
            occurred_at=sampled_at,
            action="MeterValues",
            payload={
                "meterValue": [
                    {
                        "timestamp": sampled_at,
                        "sampledValue": [
                            {
                                "value": str(row["value"]),
                                "measurand": str(row["measurand"]),
                                "unit": str(row["unit"]),
                                "multiplier": int(row["multiplier"]),
                            }
                        ],
                    }
                ]
            },
            requires_runtime_transaction_id=True,
        )


def _integer_meter(value: object) -> int:
    if value in (None, ""):
        return 0
    return int(float(str(value)))


def iter_v16_inbound_request_replay(
    database: Path,
    *,
    charger_identity: str | None = None,
    batch_size: int = 250,
) -> Iterator[ReplayEvent]:
    """Stream retained charger-originated OCPP 1.6 requests when payloads exist."""

    if batch_size < 1:
        raise ValueError("Replay batch size must be at least 1.")

    with _connect(database) as connection:
        _require_current_generation(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "ocpp_inboundprotocolrequest" not in tables:
            return

        parameters: list[object] = ["ocpp1.6", "charge_point_to_csms"]
        charger_filter = ""
        if charger_identity is not None:
            charger_filter = "AND charger.identity = ?"
            parameters.append(charger_identity)

        cursor = connection.execute(
            f"""
            SELECT request.id, request.action, request.request_payload,
                   request.received_at
            FROM ocpp_inboundprotocolrequest AS request
            JOIN ocpp_charger AS charger ON charger.id = request.charger_id
            WHERE request.version = ?
              AND request.direction = ?
              {charger_filter}
            ORDER BY request.received_at, request.id
            """,
            parameters,
        )
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                payload = json.loads(row["request_payload"])
                if not isinstance(payload, dict):
                    continue
                yield ReplayEvent(
                    source_transaction_id=-int(row["id"]),
                    occurred_at=str(row["received_at"]),
                    action=str(row["action"]),
                    payload=payload,
                )


async def run_v16_replay_events(
    transport: ReplayTransport,
    events: Iterable[ReplayEvent],
    *,
    pacing: ReplayPacing | None = None,
    after_event: Callable[[int], Awaitable[None]] | None = None,
    metrics: ReplayMetrics | None = None,
) -> tuple[str, ...]:
    """Replay ordered OCPP 1.6 events through an arbitrary live-style transport."""
    runtime_transactions: dict[int, object] = {}
    completed: list[str] = []
    pacing = pacing or ReplayPacing()
    pacing.validate()
    metrics = metrics or ReplayMetrics()

    for event in events:
        await _pace(pacing, len(completed))
        payload = dict(event.payload)
        if event.requires_runtime_transaction_id:
            runtime_id = runtime_transactions.get(event.source_transaction_id)
            if runtime_id is None:
                raise ValueError(
                    "Replay event requires a runtime transaction ID before "
                    f"transaction {event.source_transaction_id} was started."
                )
            payload["transactionId"] = runtime_id

        request_started = metrics.start_request()
        try:
            response = await transport.call(event.action, payload)
        except Exception as exc:
            metrics.record_failure(request_started, exc)
            metrics.finish()
            raise
        metrics.record_success(request_started)
        if event.action == "StartTransaction":
            runtime_id = response.get("transactionId")
            if runtime_id is None:
                raise ValueError("StartTransaction response is missing transactionId")
            runtime_transactions[event.source_transaction_id] = runtime_id
        completed.append(event.action)
        if after_event is not None:
            await after_event(len(completed))

    metrics.finish()
    return tuple(completed)


async def run_v16_live_replay_events(
    transport: ReconnectableReplayTransport,
    events: Iterable[ReplayEvent],
    *,
    reconnect_after: int | None = None,
    pacing: ReplayPacing | None = None,
    after_event: Callable[[int], Awaitable[None]] | None = None,
    metrics: ReplayMetrics | None = None,
) -> tuple[str, ...]:
    """Replay events and optionally reconnect the real charger mid-drain."""
    if reconnect_after is not None and reconnect_after < 1:
        raise ValueError("reconnect_after must be positive")

    runtime_transactions: dict[int, object] = {}
    completed: list[str] = []
    pacing = pacing or ReplayPacing()
    pacing.validate()
    metrics = metrics or ReplayMetrics()

    for event in events:
        if reconnect_after is not None and len(completed) == reconnect_after:
            reconnect_started = metrics.start_reconnect()
            try:
                await transport.reconnect()
                boot = await transport.boot()
                status = getattr(boot, "status", None)
                if status != "Accepted":
                    raise ValueError(
                        "BootNotification was not accepted after replay reconnect: "
                        f"{status}"
                    )
            except Exception as exc:
                metrics.record_reconnect_failure(reconnect_started, exc)
                metrics.finish()
                raise
            metrics.record_reconnect_success(reconnect_started)

        await _pace(pacing, len(completed))
        payload = dict(event.payload)
        if event.requires_runtime_transaction_id:
            runtime_id = runtime_transactions.get(event.source_transaction_id)
            if runtime_id is None:
                raise ValueError(
                    "Replay event requires a runtime transaction ID before "
                    f"transaction {event.source_transaction_id} was started."
                )
            payload["transactionId"] = runtime_id

        request_started = metrics.start_request()
        try:
            response = await transport.call(event.action, payload)
        except Exception as exc:
            metrics.record_failure(request_started, exc)
            metrics.finish()
            raise
        metrics.record_success(request_started)
        if event.action == "StartTransaction":
            runtime_id = response.get("transactionId")
            if runtime_id is None:
                raise ValueError("StartTransaction response is missing transactionId")
            runtime_transactions[event.source_transaction_id] = runtime_id

        completed.append(event.action)
        if after_event is not None:
            await after_event(len(completed))

    metrics.finish()
    return tuple(completed)


class _InProcessReplayTransport:
    """Adapt the retained in-process simulator to the replay transport contract."""

    def __init__(self, client: OcppSimulator) -> None:
        self.client = client

    async def call(
        self,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        response = await self.client.call(action, payload)
        return response.payload


async def run_v16_database_replay(
    charger: Charger,
    database: Path,
    *,
    source_charger_identity: str | None = None,
    batch_size: int = 250,
    pacing: ReplayPacing | None = None,
    reconnect_after: int | None = None,
    after_event: Callable[[int], Awaitable[None]] | None = None,
) -> tuple[str, ...]:
    """Replay migrated transaction history back-to-back at charger speed."""

    if reconnect_after is not None and reconnect_after < 1:
        raise ValueError("reconnect_after must be positive")

    events = iter_v16_transaction_replay(
        database,
        charger_identity=source_charger_identity,
        batch_size=batch_size,
    )
    if reconnect_after is None:
        transport = _InProcessReplayTransport(
            OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
        )
        return await run_v16_replay_events(
            transport,
            events,
            pacing=pacing,
            after_event=after_event,
        )

    # Preserve the historical in-process reconnect behavior until the live
    # reconnect-and-drain chunk replaces this seam with a real WebSocket cycle.
    runtime_transactions: dict[int, object] = {}
    completed: list[str] = []
    active = _InProcessReplayTransport(
        OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
    )
    pacing = pacing or ReplayPacing()
    pacing.validate()
    for event in events:
        if len(completed) == reconnect_after:
            active = _InProcessReplayTransport(
                OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
            )
        await _pace(pacing, len(completed))
        payload = dict(event.payload)
        if event.requires_runtime_transaction_id:
            runtime_id = runtime_transactions.get(event.source_transaction_id)
            if runtime_id is None:
                raise ValueError(
                    "Replay event requires a runtime transaction ID before "
                    f"transaction {event.source_transaction_id} was started."
                )
            payload["transactionId"] = runtime_id
        response = await active.call(event.action, payload)
        if event.action == "StartTransaction":
            runtime_id = response.get("transactionId")
            if runtime_id is None:
                raise ValueError("StartTransaction response is missing transactionId")
            runtime_transactions[event.source_transaction_id] = runtime_id
        completed.append(event.action)
        if after_event is not None:
            await after_event(len(completed))
    return tuple(completed)


async def run_v16_inbound_request_replay(
    charger: Charger,
    database: Path,
    *,
    source_charger_identity: str | None = None,
    batch_size: int = 250,
    pacing: ReplayPacing | None = None,
) -> tuple[str, ...]:
    """Replay retained inbound OCPP requests with independently controlled pacing."""

    client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
    completed: list[str] = []
    pacing = pacing or ReplayPacing()
    pacing.validate()

    for event in iter_v16_inbound_request_replay(
        database,
        charger_identity=source_charger_identity,
        batch_size=batch_size,
    ):
        await _pace(pacing, len(completed))
        await client.call(event.action, dict(event.payload))
        completed.append(event.action)

    return tuple(completed)
