from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import DecimalField, ExpressionWrapper, F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone as dj_timezone

from apps.ocpp import store
from apps.ocpp.models import Transaction, annotate_transaction_energy_bounds


@dataclass
class ExtractWindow:
    start: datetime
    end: datetime


class Command(BaseCommand):
    help = "Extract recent OCPP transactions and session logs"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--all",
            action="store_true",
            help="List all transactions with energy transfer",
        )
        parser.add_argument(
            "--next",
            type=int,
            default=10,
            help="Number of recent transactions to list (default: 10)",
        )
        parser.add_argument(
            "--txn",
            help="Transaction id or OCPP transaction id to inspect",
        )
        parser.add_argument(
            "--out",
            help="Path to write an extract file for --txn",
        )
        parser.add_argument(
            "--log",
            help="Path to write the charger log segment for --txn",
        )

    def handle(self, *args, **options) -> None:
        txn_value = options.get("txn")
        out_path = options.get("out")
        log_path = options.get("log")

        if (out_path or log_path) and not txn_value:
            raise CommandError("--out/--log require --txn")

        if txn_value:
            transaction = self._get_transaction(txn_value)
            self._describe_transaction(transaction)

            if out_path:
                extract = self._build_extract(transaction)
                Path(out_path).write_text(
                    json.dumps(extract, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Wrote extract to {out_path}")
                )

            if log_path:
                window = self._transaction_window(transaction)
                log_file = self._charger_log_file(transaction)
                if not log_file or not log_file.exists():
                    self.stdout.write(
                        self.style.WARNING("No charger log file found for transaction.")
                    )
                else:
                    lines = self._read_log_segment(log_file, window)
                    Path(log_path).write_text("\n".join(lines), encoding="utf-8")
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Wrote {len(lines)} log lines to {log_path}"
                        )
                    )
            return

        limit = None if options.get("all") else max(int(options.get("next") or 0), 1)
        transactions = self._list_transactions(limit=limit)
        if not transactions:
            self.stdout.write("No transactions with energy transfer found.")
            return

        header = "Recent transactions with energy transfer"
        if limit is not None:
            header += f" (showing {len(transactions)} of {limit})"
        self.stdout.write(header)
        for tx in transactions:
            charger = tx.charger.charger_id if tx.charger else "unknown"
            start_time = dj_timezone.localtime(tx.start_time).isoformat()
            stop_time = (
                dj_timezone.localtime(tx.stop_time).isoformat()
                if tx.stop_time
                else "ongoing"
            )
            self.stdout.write(
                "  "
                f"txn={tx.pk} charger={charger} connector={tx.connector_id or '-'} "
                f"ocpp={tx.ocpp_transaction_id or '-'} kwh={tx.kw:.3f} "
                f"start={start_time} stop={stop_time}"
            )

    def _get_transaction(self, value: str) -> Transaction:
        qs = Transaction.objects.select_related("charger").prefetch_related("meter_values")
        if value.isdigit():
            tx = qs.filter(pk=int(value)).first()
            if tx:
                return tx
        tx = qs.filter(ocpp_transaction_id=value).order_by("-start_time").first()
        if tx:
            return tx
        raise CommandError(f"Transaction not found for '{value}'")

    def _list_transactions(self, limit: int | None) -> list[Transaction]:
        qs = annotate_transaction_energy_bounds(
            Transaction.objects.select_related("charger").prefetch_related("meter_values")
        )
        kw_field = DecimalField(max_digits=12, decimal_places=3)
        qs = qs.annotate(
            energy_start=Coalesce(
                ExpressionWrapper(F("meter_start") / Value(1000.0), output_field=kw_field),
                F("meter_energy_start"),
            ),
            energy_end=Coalesce(
                ExpressionWrapper(F("meter_stop") / Value(1000.0), output_field=kw_field),
                F("meter_energy_end"),
            ),
        ).annotate(
            energy_delta=ExpressionWrapper(
                F("energy_end") - F("energy_start"),
                output_field=kw_field,
            )
        ).filter(energy_delta__gt=0).order_by("-start_time")
        if limit is None:
            return list(qs)
        return list(qs[:limit])

    def _describe_transaction(self, tx: Transaction) -> None:
        charger = tx.charger.charger_id if tx.charger else "unknown"
        lines = [
            f"Transaction {tx.pk}",
            f"  Charger: {charger}",
            f"  Connector: {tx.connector_id or '-'}",
            f"  OCPP ID: {tx.ocpp_transaction_id or '-'}",
            f"  Energy (kWh): {tx.kw:.3f}",
            f"  Meter start: {tx.meter_start}",
            f"  Meter stop: {tx.meter_stop}",
            f"  Start time: {dj_timezone.localtime(tx.start_time).isoformat()}",
            f"  Stop time: {dj_timezone.localtime(tx.stop_time).isoformat() if tx.stop_time else '-'}",
            f"  Received start: {dj_timezone.localtime(tx.received_start_time).isoformat() if tx.received_start_time else '-'}",
            f"  Received stop: {dj_timezone.localtime(tx.received_stop_time).isoformat() if tx.received_stop_time else '-'}",
            f"  Meter values: {tx.meter_values.count()}",
        ]
        log_file = self._find_session_log_file(tx)
        if log_file:
            lines.append(f"  Session log: {log_file}")
        self.stdout.write("\n".join(lines))

    def _build_extract(self, tx: Transaction) -> dict:
        charger = tx.charger
        chargers = []
        if charger:
            chargers.append(
                {
                    "charger_id": charger.charger_id,
                    "connector_id": charger.connector_id,
                    "require_rfid": charger.require_rfid,
                }
            )
        session_entries = []
        session_file = self._find_session_log_file(tx)
        if session_file and session_file.exists():
            session_entries = json.loads(session_file.read_text(encoding="utf-8"))

        transaction_entry = {
            "charger": charger.charger_id if charger else None,
            "account": tx.account_id,
            "rfid": tx.rfid,
            "vid": tx.vehicle_identifier,
            "vin": tx.vin,
            "ocpp_transaction_id": tx.ocpp_transaction_id,
            "meter_start": tx.meter_start,
            "meter_stop": tx.meter_stop,
            "voltage_start": tx.voltage_start,
            "voltage_stop": tx.voltage_stop,
            "current_import_start": tx.current_import_start,
            "current_import_stop": tx.current_import_stop,
            "current_offered_start": tx.current_offered_start,
            "current_offered_stop": tx.current_offered_stop,
            "temperature_start": tx.temperature_start,
            "temperature_stop": tx.temperature_stop,
            "soc_start": tx.soc_start,
            "soc_stop": tx.soc_stop,
            "start_time": tx.start_time.isoformat(),
            "stop_time": tx.stop_time.isoformat() if tx.stop_time else None,
            "received_start_time": tx.received_start_time.isoformat()
            if tx.received_start_time
            else None,
            "received_stop_time": tx.received_stop_time.isoformat()
            if tx.received_stop_time
            else None,
            "meter_values": [
                {
                    "connector_id": mv.connector_id,
                    "timestamp": mv.timestamp.isoformat(),
                    "context": mv.context,
                    "energy": str(mv.energy) if mv.energy is not None else None,
                    "voltage": str(mv.voltage) if mv.voltage is not None else None,
                    "current_import": (
                        str(mv.current_import) if mv.current_import is not None else None
                    ),
                    "current_offered": (
                        str(mv.current_offered)
                        if mv.current_offered is not None
                        else None
                    ),
                    "temperature": (
                        str(mv.temperature) if mv.temperature is not None else None
                    ),
                    "soc": str(mv.soc) if mv.soc is not None else None,
                }
                for mv in tx.meter_values.all()
            ],
        }
        return {
            "format": "ocpp-extract-v1",
            "generated_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "transactions": [transaction_entry],
            "chargers": chargers,
            "session_log": session_entries,
            "source": {
                "transaction_id": tx.pk,
                "charger": charger.charger_id if charger else None,
                "connector": tx.connector_id,
            },
        }

    def _transaction_window(self, tx: Transaction) -> ExtractWindow:
        start = tx.received_start_time or tx.start_time
        end = tx.received_stop_time or tx.stop_time or start
        if dj_timezone.is_naive(start):
            start = dj_timezone.make_aware(start)
        if dj_timezone.is_naive(end):
            end = dj_timezone.make_aware(end)
        if end < start:
            start, end = end, start
        return ExtractWindow(start=start, end=end)

    def _charger_log_file(self, tx: Transaction) -> Path | None:
        if not tx.charger:
            return None
        key = store.identity_key(tx.charger.charger_id, tx.connector_id)
        return store._file_path(key, log_type="charger")

    def _read_log_segment(self, log_file: Path, window: ExtractWindow) -> list[str]:
        lines: list[str] = []
        with log_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(line) < 24:
                    continue
                timestamp_raw = line[:23]
                try:
                    timestamp = datetime.strptime(
                        timestamp_raw, "%Y-%m-%d %H:%M:%S.%f"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if window.start <= timestamp <= window.end:
                    lines.append(line.rstrip("\n"))
        return lines

    def _find_session_log_file(self, tx: Transaction) -> Path | None:
        if not tx.charger:
            return None
        key = store.identity_key(tx.charger.charger_id, tx.connector_id)
        session_folder = store._session_folder(key)
        matches = list(session_folder.glob(f"*_{tx.pk}.json"))
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.stat().st_mtime)[-1]
