from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q, QuerySet
from django.utils import timezone

from ocpp import store
from ocpp.models import Charger


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
                "Charge point path used to filter chargers by their last known "
                "connection path. Matching ignores surrounding slashes."
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
        cp = options.get("cp")
        enable_rfid = options.get("rfid_enable")
        disable_rfid = options.get("rfid_disable")

        if enable_rfid and disable_rfid:
            raise CommandError("Use either --rfid-enable or --rfid-disable, not both.")

        queryset = Charger.objects.all().select_related("location", "manager_node")

        if serial:
            queryset = self._filter_by_serial(queryset, serial)
            if not queryset.exists():
                raise CommandError(
                    f"No chargers found matching serial number suffix '{serial}'."
                )

        if cp:
            queryset = self._filter_by_cp(queryset, cp)
            if not queryset.exists():
                raise CommandError(
                    f"No chargers found matching charge point path '{cp}'."
                )

        if (enable_rfid or disable_rfid) and not (serial or cp):
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

        if serial or cp:
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

    def _filter_by_cp(
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

    def _render_table(self, chargers: Iterable[Charger]) -> None:
        rows: list[dict[str, str]] = []
        for charger in chargers:
            rows.append(
                {
                    "serial": charger.charger_id,
                    "name": charger.display_name or "-",
                    "connector": (
                        str(charger.connector_id)
                        if charger.connector_id is not None
                        else "all"
                    ),
                    "rfid": "on" if charger.require_rfid else "off",
                    "public": "yes" if charger.public_display else "no",
                    "connected": "yes"
                    if store.is_connected(charger.charger_id, charger.connector_id)
                    else "no",
                    "status": charger.last_status or "-",
                }
            )

        headers = {
            "serial": "Serial",
            "name": "Name",
            "connector": "Connector",
            "rfid": "RFID",
            "public": "Public",
            "connected": "Connected",
            "status": "Last Status",
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
            connector_label = (
                f"connector {charger.connector_id}"
                if charger.connector_id is not None
                else "all connectors"
            )
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
    def _format_dt(value: datetime | None) -> str | None:
        if not value:
            return None
        if timezone.is_aware(value):
            return timezone.localtime(value).isoformat()
        return value.isoformat()
