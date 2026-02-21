from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.ocpp import store
from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_trace_replay_arguments,
    warn_deprecated_command,
)
from apps.ocpp.transactions_io import import_transactions_deduped


@dataclass
class ReplayResult:
    """Result payload for replaying an extracted trace."""

    imported: int
    skipped: int
    session_log_written: bool


def run_replay_extract(*, extract: str) -> ReplayResult:
    """Replay an OCPP extract file into local transaction storage."""
    extract_path = Path(extract)
    if not extract_path.exists():
        raise CommandError(f"Extract file not found: {extract_path}")
    data = json.loads(extract_path.read_text(encoding="utf-8"))

    if data.get("format") != "ocpp-extract-v1":
        raise CommandError("Unsupported extract format.")

    imported, skipped, imported_transactions = import_transactions_deduped(
        {"chargers": data.get("chargers", []), "transactions": data.get("transactions", [])}
    )

    session_log_written = False
    session_entries = data.get("session_log", [])
    if imported_transactions and session_entries:
        transaction = imported_transactions[0]
        session_path = _session_log_path(transaction)
        if not session_path.exists():
            session_path.write_text(
                json.dumps(session_entries, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            session_log_written = True

    return ReplayResult(
        imported=imported,
        skipped=skipped,
        session_log_written=session_log_written,
    )


def _session_log_path(transaction) -> Path:
    """Resolve the session-log output path for a transaction."""
    start_time = transaction.start_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    date = start_time.astimezone(timezone.utc).strftime("%Y%m%d")
    if not transaction.charger:
        folder = store._session_folder(store.AGGREGATE_SLUG)
    else:
        key = store.identity_key(transaction.charger.charger_id, transaction.connector_id)
        folder = store._session_folder(key)
    return folder / f"{date}_{transaction.pk}.json"


class Command(BaseCommand):
    help = "Replay an extracted OCPP transaction into the system"

    def add_arguments(self, parser) -> None:
        add_trace_replay_arguments(parser)

    def handle(self, *args, **options) -> None:
        warn_deprecated_command("ocpp_replay", "ocpp trace replay")
        call_command("ocpp", "trace", "replay", options["extract"])
