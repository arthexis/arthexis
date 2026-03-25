from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import Charger


class ChargersRenderer:
    """Pure rendering helpers for chargers command output."""

    def __init__(self, command):
        self.command = command

    def render_tail(self, charger: Charger, limit: int) -> None:
        connector_label = self.connector_descriptor(charger)
        heading = f"Log tail ({connector_label}; last {limit} entries)"
        self.command.stdout.write("")
        self.command.stdout.write(self.command.style.MIGRATE_HEADING(heading))

        log_key = store.identity_key(charger.charger_id, charger.connector_id)
        entries = store.get_logs(log_key)

        if not entries:
            self.command.stdout.write("No log entries recorded.")
            return

        for line in entries[-limit:]:
            self.command.stdout.write(line)

    def render_sessions(self, chargers: Iterable[Charger], limit: int) -> None:
        entries = self.collect_session_entries(chargers)
        if not entries:
            self.command.stdout.write("No session logs found.")
            return

        entries.sort(key=lambda item: item["timestamp"], reverse=True)
        selected = entries[:limit]
        total_count = len(entries)
        heading = "Recent sessions"
        if total_count > limit:
            heading += f" (showing {len(selected)} of {total_count})"
        self.command.stdout.write(self.command.style.MIGRATE_HEADING(heading))
        for entry in selected:
            charger = entry["charger"]
            connector_label = self.connector_descriptor(charger)
            label = charger.display_name or charger.charger_id
            timestamp = self.command._format_dt(entry["timestamp"]) or "-"
            tx_id = entry["tx_id"] or "-"
            self.command.stdout.write(
                f"{timestamp}  {label} ({connector_label})  tx={tx_id}  {entry['path']}"
            )

    def render_table(self, chargers: Iterable[Charger]) -> None:
        totals: dict[int, float] = {}
        aggregate_totals: dict[str, float] = {}
        aggregate_sources: set[str] = set()

        for charger in chargers:
            total = self.command._total_energy_kwh(charger)
            totals[charger.pk] = total
            if charger.connector_id is not None:
                aggregate_sources.add(charger.charger_id)
                aggregate_totals[charger.charger_id] = (
                    aggregate_totals.get(charger.charger_id, 0.0) + total
                )

        rows: list[dict[str, str]] = []
        for charger in chargers:
            total = totals.get(charger.pk, 0.0)
            if charger.connector_id is None and charger.charger_id in aggregate_sources:
                total = aggregate_totals.get(charger.charger_id, total)
            status_label = self.command._status_label(charger)
            rfid_value = "on" if charger.require_rfid else "off"
            if charger.connector_id is not None and status_label.casefold() == "charging":
                tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
                if tx_obj is not None:
                    active_rfid = str(getattr(tx_obj, "rfid", "") or "").strip()
                    if active_rfid:
                        rfid_value = active_rfid.upper()
            last_contact = self.command._last_contact_timestamp(charger)
            rows.append(
                {
                    "serial": charger.charger_id,
                    "name": charger.display_name or "-",
                    "connector": (
                        Charger.connector_letter_from_value(charger.connector_id)
                        if charger.connector_id is not None
                        else "all"
                    ),
                    "rfid": rfid_value,
                    "public": "yes" if charger.public_display else "no",
                    "status": status_label,
                    "energy": self.command._format_energy(total),
                    "last_contact": self.command._format_dt(last_contact) or "-",
                }
            )

        headers = {
            "serial": "Serial",
            "name": "Name",
            "connector": "Connector",
            "rfid": "RFID",
            "public": "Public",
            "status": "Status",
            "energy": "Total Energy (kWh)",
            "last_contact": "Last Contact",
        }

        widths = {
            key: max(len(headers[key]), *(len(row[key]) for row in rows))
            for key in headers
        }

        header_line = "  ".join(headers[key].ljust(widths[key]) for key in headers)
        separator = "  ".join("-" * widths[key] for key in headers)
        self.command.stdout.write(header_line)
        self.command.stdout.write(separator)
        for row in rows:
            self.command.stdout.write(
                "  ".join(row[key].ljust(widths[key]) for key in headers)
            )

    def render_details(self, chargers: Iterable[Charger]) -> None:
        for idx, charger in enumerate(chargers):
            if idx:
                self.command.stdout.write("")

            heading = charger.display_name or charger.charger_id
            connector_label = self.connector_descriptor(charger)
            heading_text = f"{heading} ({connector_label})"
            self.command.stdout.write(self.command.style.MIGRATE_HEADING(heading_text))

            info: list[tuple[str, str]] = [
                ("Serial", charger.charger_id),
                (
                    "Connected",
                    (
                        "Yes"
                        if store.is_connected(charger.charger_id, charger.connector_id)
                        else "No"
                    ),
                ),
                ("Require RFID", "Yes" if charger.require_rfid else "No"),
                ("Public Display", "Yes" if charger.public_display else "No"),
                ("Location", charger.location.name if charger.location else "-"),
                (
                    "Manager Node",
                    charger.manager_node.hostname if charger.manager_node else "-",
                ),
                ("Last Heartbeat", self.command._format_dt(charger.last_heartbeat) or "-"),
                ("Last Status", charger.last_status or "-"),
                (
                    "Last Status Timestamp",
                    self.command._format_dt(charger.last_status_timestamp) or "-",
                ),
                ("Last Error Code", charger.last_error_code or "-"),
                ("Availability State", charger.availability_state or "-"),
                ("Requested State", charger.availability_requested_state or "-"),
                ("Request Status", charger.availability_request_status or "-"),
                ("Firmware Status", charger.firmware_status or "-"),
                ("Firmware Info", charger.firmware_status_info or "-"),
                (
                    "Firmware Timestamp",
                    self.command._format_dt(charger.firmware_timestamp) or "-",
                ),
                ("Last Path", charger.last_path or "-"),
            ]

            for label, value in info:
                self.command.stdout.write(f"{label}: {value}")

            if charger.last_status_vendor_info:
                vendor_info = json.dumps(
                    charger.last_status_vendor_info, indent=2, sort_keys=True
                )
                self.command.stdout.write("Vendor Info:")
                self.command.stdout.write(vendor_info)

            if charger.last_meter_values:
                self.render_last_meter_values(charger.last_meter_values)

    def render_last_meter_values(self, payload: dict) -> None:
        self.command.stdout.write("Last Meter Values:")
        if not isinstance(payload, dict):
            self.command.stdout.write("  -")
            return

        self.render_meter_values_transaction(payload)

        meter_values = payload.get("meterValue")
        if not isinstance(meter_values, list) or not meter_values:
            self.command.stdout.write("  No meter values reported.")
            return

        total = len(meter_values)
        for idx, entry in enumerate(meter_values, start=1):
            self.render_meter_value_entry(entry, idx, total)

    def render_meter_values_transaction(self, payload: dict) -> None:
        transaction_id = payload.get("transactionId")
        if transaction_id is not None:
            self.command.stdout.write(f"  Transaction ID: {transaction_id}")

    def render_meter_value_entry(self, entry: object, index: int, total: int) -> None:
        if not isinstance(entry, dict):
            return
        timestamp = entry.get("timestamp")
        if timestamp:
            label = "Timestamp" if total <= 1 else f"Timestamp {index}"
            self.command.stdout.write(f"  {label}: {timestamp}")

        sampled_values = entry.get("sampledValue")
        if not isinstance(sampled_values, list):
            return
        for sample in sampled_values:
            self.render_sampled_value(sample)

    def render_sampled_value(self, sample: object) -> None:
        if not isinstance(sample, dict):
            return
        measurand = sample.get("measurand") or "Value"
        value_text = self.format_sample_value(sample.get("value"), sample.get("unit"))
        meta_text = self.format_sample_meta(sample.get("context"), sample.get("location"))
        self.command.stdout.write(f"  - {measurand}: {value_text}{meta_text}")

    @staticmethod
    def connector_descriptor(charger: Charger) -> str:
        if charger.connector_id is None:
            return "all connectors"
        letter = Charger.connector_letter_from_value(charger.connector_id)
        if letter:
            return f"connector {letter}"
        return f"connector {charger.connector_id}"

    @staticmethod
    def format_sample_meta(context: object, location: object) -> str:
        meta_parts: list[str] = []
        if context:
            meta_parts.append(f"context: {context}")
        if location:
            meta_parts.append(f"location: {location}")
        return f" ({', '.join(meta_parts)})" if meta_parts else ""

    @staticmethod
    def format_sample_value(value: object, unit: object) -> str:
        value_parts: list[str] = []
        if value is not None:
            value_parts.append(str(value))
        if unit:
            value_parts.append(str(unit))
        return " ".join(value_parts) if value_parts else "-"

    def collect_session_entries(self, chargers: Iterable[Charger]) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for charger in chargers:
            for folder in self.session_folders_for_charger(charger):
                for path in folder.glob("*.json"):
                    if not path.is_file():
                        continue
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        continue
                    timestamp = datetime.fromtimestamp(stat.st_mtime, tz=dt_timezone.utc)
                    entries.append(
                        {
                            "timestamp": timezone.localtime(timestamp),
                            "charger": charger,
                            "path": path,
                            "tx_id": self.session_transaction_id(path.name),
                        }
                    )
        return entries

    @staticmethod
    def safe_session_name(name: str) -> str:
        return re.sub(r"[^\w.-]", "_", name)

    @staticmethod
    def session_transaction_id(filename: str) -> str | None:
        stem = filename.rsplit(".", 1)[0]
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
        return None

    def session_folders_for_charger(self, charger: Charger) -> list[Path]:
        identity_key = store.identity_key(charger.charger_id, charger.connector_id)
        pending_key = store.pending_key(charger.charger_id)
        candidates = {charger.charger_id, identity_key, pending_key}
        if charger.display_name:
            candidates.add(charger.display_name)
        if charger.name:
            candidates.add(charger.name)
        log_names = store.log_names.get("charger", {})
        for key in (charger.charger_id, identity_key, pending_key):
            registered = log_names.get(key)
            if registered:
                candidates.add(registered)
        folders = []
        for name in candidates:
            safe_name = self.safe_session_name(name)
            path = store.SESSION_DIR / safe_name
            if path.exists() and path.is_dir():
                folders.append(path)
        return folders
