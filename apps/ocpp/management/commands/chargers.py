from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ocpp import store
from apps.ocpp.models import Charger, MeterValue, Transaction
from apps.ocpp.views import _aggregate_dashboard_state


class Command(BaseCommand):
    help = "Inspect configured OCPP chargers and update their RFID settings."

    def add_arguments(self, parser) -> None:  # pragma: no cover - simple wiring
        parser.add_argument(
            "--sn",
            dest="serial",
            help=(
                "Serial number (or suffix) used to narrow the charger selection. "
                "Matching is case-insensitive and falls back to helpful suffix "
                "matching."
            ),
        )
        parser.add_argument(
            "-cp",
            "--cp",
            dest="cp",
            help=(
                "Connector identifier used to filter chargers. Provide a connector "
                "number or 'all' to select aggregate connectors. Non-numeric "
                "values fall back to matching the charge point path, ignoring "
                "surrounding slashes."
            ),
        )
        parser.add_argument(
            "--tail",
            dest="tail",
            type=int,
            nargs="?",
            const=20,
            help=(
                "Show the last N log entries for the selected charger/connector. "
                "Defaults to 20 entries when no value is provided."
            ),
        )
        parser.add_argument(
            "--rfid-enable",
            action="store_true",
            help="Enable the RFID authentication requirement for the matched chargers.",
        )
        parser.add_argument(
            "--rfid-disable",
            action="store_true",
            help=(
                "Disable the RFID authentication requirement for the matched "
                "chargers."
            ),
        )

    def handle(self, *args, **options):
        serial = options.get("serial")
        cp_raw = options.get("cp")
        tail = options.get("tail")
        enable_rfid = options.get("rfid_enable")
        disable_rfid = options.get("rfid_disable")

        if enable_rfid and disable_rfid:
            raise CommandError("Use either --rfid-enable or --rfid-disable, not both.")

        if tail is not None and tail <= 0:
            raise CommandError("--tail requires a positive number of log entries.")

        queryset = (
            Charger.objects.all()
            .select_related("location", "manager_node")
            .prefetch_related(self._transaction_prefetch())
        )

        if serial:
            queryset = self._filter_by_serial(queryset, serial)
            if not queryset.exists():
                raise CommandError(
                    f"No chargers found matching serial number suffix '{serial}'."
                )

        connector_filter = None
        cp_path = None
        if cp_raw:
            connector_filter, cp_path = self._parse_cp(cp_raw)

        if connector_filter is not None:
            queryset = self._filter_by_connector(queryset, connector_filter)
            match_count = queryset.count()
            if not match_count:
                if connector_filter == store.AGGREGATE_SLUG:
                    raise CommandError(
                        "No chargers found matching aggregate connector 'all'."
                    )
                raise CommandError(
                    f"No chargers found matching connector '{cp_raw}'."
                )
            if match_count > 1:
                self.stdout.write(
                    self.style.WARNING(
                        "Multiple chargers matched the provided connector id; showing all matches."
                    )
                )

        if cp_path:
            queryset = self._filter_by_cp_path(queryset, cp_path)
            if not queryset.exists():
                raise CommandError(
                    f"No chargers found matching charge point path '{cp_path}'."
                )

        if (enable_rfid or disable_rfid) and not (serial or cp_raw):
            raise CommandError(
                "RFID toggles require selecting at least one charger with --sn and/or --cp."
            )

        chargers = list(queryset.order_by("charger_id", "connector_id"))

        if not chargers:
            self.stdout.write("No chargers found.")
            return

        if enable_rfid or disable_rfid:
            new_value = bool(enable_rfid)
            updated = queryset.update(require_rfid=new_value)
            verb = "Enabled" if new_value else "Disabled"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} RFID authentication on {updated} charger(s)."
                )
            )
            # Refresh to reflect the updated state for output below.
            chargers = list(
                Charger.objects.filter(pk__in=[c.pk for c in chargers]).select_related(
                    "location", "manager_node"
                )
            )

        if tail is not None:
            if len(chargers) != 1:
                raise CommandError(
                    "--tail requires selecting exactly one charger using --sn and/or --cp."
                )
            self._render_details(chargers)
            self._render_tail(chargers[0], tail)
            return

        if serial or cp_raw:
            self._render_details(chargers)
        else:
            self._render_table(chargers)

    def _filter_by_serial(
        self, queryset: QuerySet[Charger], serial: str
    ) -> QuerySet[Charger]:
        normalized = Charger.normalize_serial(serial)
        if not normalized:
            return queryset.none()

        for lookup in ("iexact", "iendswith", "icontains"):
            filtered = queryset.filter(**{f"charger_id__{lookup}": normalized})
            if filtered.exists():
                if lookup != "iexact" and filtered.count() > 1:
                    self.stdout.write(
                        self.style.WARNING(
                            "Multiple chargers matched the provided serial suffix; "
                            "showing all matches."
                        )
                    )
                return filtered
        return queryset.none()

    def _filter_by_cp_path(
        self, queryset: QuerySet[Charger], cp: str
    ) -> QuerySet[Charger]:
        normalized = (cp or "").strip().strip("/")
        if not normalized:
            return queryset.none()

        patterns = {normalized, f"/{normalized}", f"{normalized}/", f"/{normalized}/"}
        filters = Q()
        for pattern in patterns:
            filters |= Q(last_path__iexact=pattern)
        filtered = queryset.filter(filters)
        if filtered.exists():
            return filtered

        suffix_filters = Q()
        for pattern in patterns:
            suffix_filters |= Q(last_path__iendswith=pattern)
        suffix_filtered = queryset.filter(suffix_filters)
        if suffix_filtered.exists():
            if suffix_filtered.count() > 1:
                self.stdout.write(
                    self.style.WARNING(
                        "Multiple chargers matched the provided charge point path; "
                        "showing all matches."
                    )
                )
            return suffix_filtered

        return queryset.none()

    def _filter_by_connector(
        self, queryset: QuerySet[Charger], connector: int | str
    ) -> QuerySet[Charger]:
        if connector == store.AGGREGATE_SLUG:
            return queryset.filter(connector_id__isnull=True)
        return queryset.filter(connector_id=connector)

    def _parse_cp(self, value: str) -> tuple[int | str | None, str | None]:
        normalized = (value or "").strip()
        if not normalized:
            return None, None

        lowered = normalized.lower()
        if lowered == store.AGGREGATE_SLUG:
            return store.AGGREGATE_SLUG, None

        try:
            return Charger.connector_value_from_letter(normalized), None
        except ValueError:
            pass

        try:
            connector = int(normalized)
        except ValueError:
            return None, normalized

        if connector <= 0:
            raise CommandError(
                "--cp requires a connector identifier (A, B, ...)."
            )

        return connector, None

    def _render_tail(self, charger: Charger, limit: int) -> None:
        connector_label = self._connector_descriptor(charger)
        heading = f"Log tail ({connector_label}; last {limit} entries)"
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(heading))

        log_key = store.identity_key(charger.charger_id, charger.connector_id)
        entries = store.get_logs(log_key)

        if not entries:
            self.stdout.write("No log entries recorded.")
            return

        for line in entries[-limit:]:
            self.stdout.write(line)

    def _transaction_prefetch(self) -> Prefetch:
        return Prefetch(
            "transactions",
            queryset=Transaction.objects.all().prefetch_related(
                Prefetch(
                    "meter_values",
                    queryset=(
                        MeterValue.objects.filter(energy__isnull=False)
                        .order_by("timestamp")
                    ),
                    to_attr="energy_values",
                )
            ),
        )

    def _status_label(self, charger: Charger) -> str:
        if charger.connector_id is None:
            aggregate_state = _aggregate_dashboard_state(charger)
            if aggregate_state is not None:
                label, _color = aggregate_state
                return label
        if charger.last_status:
            return charger.last_status
        if charger.availability_state:
            return charger.availability_state
        return "-"

    @staticmethod
    def _format_energy(total: float) -> str:
        return f"{total:.2f}"

    def _total_energy_kwh(self, charger: Charger) -> float:
        total = 0.0
        connector = charger.connector_id
        for tx in charger.transactions.all():
            if connector is not None and tx.connector_id not in (None, connector):
                continue
            total += self._transaction_energy_kwh(tx)
        return total

    def _transaction_energy_kwh(self, tx: Transaction) -> float:
        start_val = None
        if tx.meter_start is not None:
            start_val = float(tx.meter_start) / 1000.0

        end_val = None
        if tx.meter_stop is not None:
            end_val = float(tx.meter_stop) / 1000.0

        readings = getattr(tx, "energy_values", None)
        if readings is None:
            readings = list(
                tx.meter_values.filter(energy__isnull=False).order_by("timestamp")
            )

        if readings:
            if start_val is None:
                start_val = float(readings[0].energy or 0)
            if end_val is None:
                end_val = float(readings[-1].energy or 0)

        if start_val is None or end_val is None:
            return 0.0

        total = end_val - start_val
        return total if total >= 0 else 0.0

    def _render_table(self, chargers: Iterable[Charger]) -> None:
        totals: dict[int, float] = {}
        aggregate_totals: dict[str, float] = {}
        aggregate_sources: set[str] = set()

        for charger in chargers:
            total = self._total_energy_kwh(charger)
            totals[charger.pk] = total
            if charger.connector_id is not None:
                aggregate_sources.add(charger.charger_id)
                aggregate_totals[charger.charger_id] = (
                    aggregate_totals.get(charger.charger_id, 0.0) + total
                )

        rows: list[dict[str, str]] = []
        for charger in chargers:
            total = totals.get(charger.pk, 0.0)
            if (
                charger.connector_id is None
                and charger.charger_id in aggregate_sources
            ):
                total = aggregate_totals.get(charger.charger_id, total)
            status_label = self._status_label(charger)
            rfid_value = "on" if charger.require_rfid else "off"
            if (
                charger.connector_id is not None
                and status_label.casefold() == "charging"
            ):
                tx_obj = store.get_transaction(
                    charger.charger_id, charger.connector_id
                )
                if tx_obj is not None:
                    active_rfid = str(getattr(tx_obj, "rfid", "") or "").strip()
                    if active_rfid:
                        rfid_value = active_rfid.upper()
            last_contact = self._last_contact_timestamp(charger)
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
                    "energy": self._format_energy(total),
                    "last_contact": self._format_dt(last_contact) or "-",
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
        self.stdout.write(header_line)
        self.stdout.write(separator)
        for row in rows:
            self.stdout.write(
                "  ".join(row[key].ljust(widths[key]) for key in headers)
            )

    def _render_details(self, chargers: Iterable[Charger]) -> None:
        for idx, charger in enumerate(chargers):
            if idx:
                self.stdout.write("")

            heading = charger.display_name or charger.charger_id
            connector_label = self._connector_descriptor(charger)
            heading_text = f"{heading} ({connector_label})"
            self.stdout.write(self.style.MIGRATE_HEADING(heading_text))

            info: list[tuple[str, str]] = [
                ("Serial", charger.charger_id),
                (
                    "Connected",
                    "Yes"
                    if store.is_connected(charger.charger_id, charger.connector_id)
                    else "No",
                ),
                ("Require RFID", "Yes" if charger.require_rfid else "No"),
                ("Public Display", "Yes" if charger.public_display else "No"),
                (
                    "Location",
                    charger.location.name if charger.location else "-",
                ),
                (
                    "Manager Node",
                    charger.manager_node.hostname if charger.manager_node else "-",
                ),
                (
                    "Last Heartbeat",
                    self._format_dt(charger.last_heartbeat) or "-",
                ),
                ("Last Status", charger.last_status or "-"),
                (
                    "Last Status Timestamp",
                    self._format_dt(charger.last_status_timestamp) or "-",
                ),
                ("Last Error Code", charger.last_error_code or "-"),
                (
                    "Availability State",
                    charger.availability_state or "-",
                ),
                (
                    "Requested State",
                    charger.availability_requested_state or "-",
                ),
                (
                    "Request Status",
                    charger.availability_request_status or "-",
                ),
                (
                    "Firmware Status",
                    charger.firmware_status or "-",
                ),
                (
                    "Firmware Info",
                    charger.firmware_status_info or "-",
                ),
                (
                    "Firmware Timestamp",
                    self._format_dt(charger.firmware_timestamp) or "-",
                ),
                ("Last Path", charger.last_path or "-"),
            ]

            for label, value in info:
                self.stdout.write(f"{label}: {value}")

            if charger.last_status_vendor_info:
                vendor_info = json.dumps(charger.last_status_vendor_info, indent=2, sort_keys=True)
                self.stdout.write("Vendor Info:")
                self.stdout.write(vendor_info)

            if charger.last_meter_values:
                meter_values = json.dumps(
                    charger.last_meter_values,
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
                self.stdout.write("Last Meter Values:")
                self.stdout.write(meter_values)

    @staticmethod
    def _connector_descriptor(charger: Charger) -> str:
        if charger.connector_id is None:
            return "all connectors"
        letter = Charger.connector_letter_from_value(charger.connector_id)
        if letter:
            return f"connector {letter}"
        return f"connector {charger.connector_id}"

    @staticmethod
    def _format_dt(value: datetime | None) -> str | None:
        if not value:
            return None
        if timezone.is_aware(value):
            return timezone.localtime(value).isoformat()
        return value.isoformat()

    def _last_contact_timestamp(self, charger: Charger) -> datetime | None:
        heartbeat = charger.last_heartbeat
        meter_ts = self._last_meter_value_timestamp(charger.last_meter_values)
        if heartbeat and meter_ts:
            return max(heartbeat, meter_ts)
        return heartbeat or meter_ts

    def _last_meter_value_timestamp(self, payload: dict | None) -> datetime | None:
        if not payload:
            return None
        entries = payload.get("meterValue")
        if not isinstance(entries, list):
            return None

        latest: datetime | None = None
        for entry in entries:
            ts_raw = None
            if isinstance(entry, dict):
                ts_raw = entry.get("timestamp")
            if not ts_raw:
                continue
            ts = parse_datetime(str(ts_raw))
            if ts is None:
                continue
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts, timezone.get_current_timezone())
            if latest is None or ts > latest:
                latest = ts
        return latest
