"""Read-only replay sources backed by migrated Arthexis databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.simulator.client import OcppSimulator


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


async def run_v16_database_replay(
    charger: Charger,
    database: Path,
    *,
    source_charger_identity: str | None = None,
    batch_size: int = 250,
) -> tuple[str, ...]:
    """Replay migrated transaction history back-to-back at charger speed."""

    client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
    runtime_transactions: dict[int, object] = {}
    completed: list[str] = []

    for event in iter_v16_transaction_replay(
        database,
        charger_identity=source_charger_identity,
        batch_size=batch_size,
    ):
        payload = dict(event.payload)
        if event.requires_runtime_transaction_id:
            runtime_id = runtime_transactions.get(event.source_transaction_id)
            if runtime_id is None:
                raise ValueError(
                    "Replay event requires a runtime transaction ID before "
                    f"transaction {event.source_transaction_id} was started."
                )
            payload["transactionId"] = runtime_id

        response = await client.call(event.action, payload)
        if event.action == "StartTransaction":
            runtime_id = response.payload.get("transactionId")
            if runtime_id is None:
                raise ValueError("StartTransaction response is missing transactionId")
            runtime_transactions[event.source_transaction_id] = runtime_id
        completed.append(event.action)

    return tuple(completed)
