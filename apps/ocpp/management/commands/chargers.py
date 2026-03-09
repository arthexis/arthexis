from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Iterable

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
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
                "number or 'all' to select all connector charge points. Non-numeric "
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
            "--sessions",
            dest="sessions",
            type=int,
            nargs="?",
            const=10,
            help=(
                "Show the last N session logs for the selected chargers. Defaults to "
                "10 sessions when no value is provided."
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
        parser.add_argument(
            "--ws-auth-username",
            dest="ws_auth_username",
            help=(
                "Username used for HTTP Basic websocket authentication. Requires "
                "--ws-auth-password and at least one charger selector."
            ),
        )
        parser.add_argument(
            "--ws-auth-password",
            dest="ws_auth_password",
            help=(
                "Password for --ws-auth-username. The user is created when missing "
                "and password is updated when present."
            ),
        )
        parser.add_argument(
            "--ws-auth-clear",
            action="store_true",
            help=(
                "Remove HTTP Basic websocket protection from the matched chargers "
                "(clears both user and group rules)."
            ),
        )
        parser.add_argument(
            "--rename",
            nargs="?",
            const="",
            help=(
                "Rename the selected charger display name. Provide a value to rename "
                "non-interactively, or pass --rename with no value for an interactive prompt."
            ),
        )
        parser.add_argument(
            "--send-stop",
            action="store_true",
            help="Send a remote stop request to the selected charger(s).",
        )
        parser.add_argument(
            "--send-restart",
            action="store_true",
            help="Send a soft reset request to the selected charger(s).",
        )
        parser.add_argument(
            "--default-base",
            action="store_true",
            help="Select the first available base charger when no selector is provided.",
        )

    def handle(self, *args, **options):
        serial = options.get("serial")
        cp_raw = options.get("cp")
        tail = options.get("tail")
        sessions = options.get("sessions")
        enable_rfid = options.get("rfid_enable")
        disable_rfid = options.get("rfid_disable")
        ws_auth_username = (options.get("ws_auth_username") or "").strip()
        ws_auth_password = options.get("ws_auth_password")
        ws_auth_clear = options.get("ws_auth_clear")
        rename_value = options.get("rename")
        send_stop = bool(options.get("send_stop"))
        send_restart = bool(options.get("send_restart"))
        use_default_base = bool(options.get("default_base"))

        self._validate_option_combinations(
            enable_rfid=enable_rfid,
            disable_rfid=disable_rfid,
            ws_auth_username=ws_auth_username,
            ws_auth_password=ws_auth_password,
            ws_auth_clear=ws_auth_clear,
            send_stop=send_stop,
            send_restart=send_restart,
            tail=tail,
            sessions=sessions,
        )

        queryset = (
            Charger.objects.all()
            .select_related("location", "manager_node")
            .prefetch_related(self._transaction_prefetch())
        )

        if use_default_base and not serial and not cp_raw:
            default_charger = self._resolve_default_base_charger(queryset)
            if not default_charger:
                self.stdout.write("No chargers found.")
                return
            queryset = queryset.filter(pk=default_charger.pk)
            serial = default_charger.charger_id

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
                        "No charge points found matching station connector selector 'all'."
                    )
                raise CommandError(f"No chargers found matching connector '{cp_raw}'.")
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

        has_charger_selector = (
            bool(serial) or connector_filter is not None or bool(cp_path)
        )

        self._validate_selector_requirements(
            has_charger_selector=has_charger_selector,
            enable_rfid=enable_rfid,
            disable_rfid=disable_rfid,
            ws_auth_username=ws_auth_username,
            ws_auth_clear=ws_auth_clear,
            rename_value=rename_value,
            send_stop=send_stop,
            send_restart=send_restart,
        )

        chargers = list(queryset.order_by("charger_id", "connector_id"))

        if not chargers:
            self.stdout.write("No chargers found.")
            return

        if self._handle_mutating_actions(
            chargers=chargers,
            queryset=queryset,
            enable_rfid=enable_rfid,
            disable_rfid=disable_rfid,
            ws_auth_username=ws_auth_username,
            ws_auth_password=ws_auth_password,
            ws_auth_clear=ws_auth_clear,
            rename_value=rename_value,
            send_stop=send_stop,
            send_restart=send_restart,
        ):
            return

        if tail is not None:
            if len(chargers) != 1:
                raise CommandError(
                    "--tail requires selecting exactly one charger using --sn and/or --cp."
                )
            self._render_details(chargers)
            self._render_tail(chargers[0], tail)
            return

        if sessions is not None:
            self._render_sessions(chargers, sessions)
            return

        if serial or cp_raw:
            self._render_details(chargers)
        else:
            self._render_table(chargers)

    def _validate_option_combinations(
        self,
        *,
        enable_rfid: bool,
        disable_rfid: bool,
        ws_auth_username: str,
        ws_auth_password: str | None,
        ws_auth_clear: bool,
        send_stop: bool,
        send_restart: bool,
        tail: int | None,
        sessions: int | None,
    ) -> None:
        if enable_rfid and disable_rfid:
            raise CommandError("Use either --rfid-enable or --rfid-disable, not both.")

        if ws_auth_username and not ws_auth_password:
            raise CommandError("--ws-auth-username requires --ws-auth-password.")

        if ws_auth_password and not ws_auth_username:
            raise CommandError("--ws-auth-password requires --ws-auth-username.")

        if ws_auth_clear and ws_auth_username:
            raise CommandError(
                "Use either --ws-auth-clear or --ws-auth-username/--ws-auth-password, not both."
            )

        if send_stop and send_restart:
            raise CommandError("Use either --send-stop or --send-restart, not both.")

        if tail is not None and tail <= 0:
            raise CommandError("--tail requires a positive number of log entries.")

        if sessions is not None and sessions <= 0:
            raise CommandError("--sessions requires a positive number of sessions.")

    def _validate_selector_requirements(
        self,
        *,
        has_charger_selector: bool,
        enable_rfid: bool,
        disable_rfid: bool,
        ws_auth_username: str,
        ws_auth_clear: bool,
        rename_value: str | None,
        send_stop: bool,
        send_restart: bool,
    ) -> None:
        if (enable_rfid or disable_rfid) and not has_charger_selector:
            raise CommandError(
                "RFID toggles require selecting at least one charger with --sn and/or --cp."
            )

        if (ws_auth_username or ws_auth_clear) and not has_charger_selector:
            raise CommandError(
                "Websocket auth changes require selecting at least one charger with --sn and/or --cp."
            )

        if (
            rename_value is not None or send_stop or send_restart
        ) and not has_charger_selector:
            raise CommandError(
                "This action requires selecting at least one charger with --sn and/or --cp."
            )

    def _handle_mutating_actions(
        self,
        *,
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        enable_rfid: bool,
        disable_rfid: bool,
        ws_auth_username: str,
        ws_auth_password: str | None,
        ws_auth_clear: bool,
        rename_value: str | None,
        send_stop: bool,
        send_restart: bool,
    ) -> bool:
        if not (
            enable_rfid
            or disable_rfid
            or ws_auth_username
            or ws_auth_clear
            or rename_value is not None
            or send_stop
            or send_restart
        ):
            return False

        selected_chargers = chargers
        selected_queryset = queryset
        aggregate_target: Charger | None = None

        if rename_value is not None or send_restart:
            aggregate_target = self._resolve_aggregate_charger(chargers)

        if enable_rfid or disable_rfid:
            new_value = bool(enable_rfid)
            updated = selected_queryset.update(require_rfid=new_value)
            verb = "Enabled" if new_value else "Disabled"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} RFID authentication on {updated} charger(s)."
                )
            )
            selected_chargers = self._reload_chargers(selected_chargers)

        if ws_auth_username:
            ws_auth_user = self._upsert_ws_auth_user(
                username=ws_auth_username,
                password=ws_auth_password,
            )
            updated = selected_queryset.update(
                ws_auth_user=ws_auth_user, ws_auth_group=None
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enabled websocket auth on {updated} charger(s) with user '{ws_auth_username}'."
                )
            )
            selected_chargers = self._reload_chargers(selected_chargers)

        if ws_auth_clear:
            updated = selected_queryset.update(ws_auth_user=None, ws_auth_group=None)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cleared websocket auth protection on {updated} charger(s)."
                )
            )
            selected_chargers = self._reload_chargers(selected_chargers)

        if rename_value is not None:
            rename_targets = (
                [aggregate_target] if aggregate_target is not None else chargers
            )
            if len(rename_targets) != 1:
                raise CommandError(
                    "--rename requires selecting exactly one charger using --sn and/or --cp."
                )
            renamed = self._rename_charger(
                rename_targets[0],
                rename_value,
                interactive=rename_value == "",
            )
            self.stdout.write(
                self.style.SUCCESS(f"Renamed charger to '{renamed.display_name}'.")
            )

        if send_stop:
            sent = self._send_stop(chargers)
            self.stdout.write(
                self.style.SUCCESS(f"Sent remote stop request to {sent} charger(s).")
            )

        if send_restart:
            restart_targets = (
                [aggregate_target] if aggregate_target is not None else chargers
            )
            sent = self._send_restart(restart_targets)
            self.stdout.write(
                self.style.SUCCESS(f"Sent reset request to {sent} charger(s).")
            )

        return True

    def _reload_chargers(self, chargers: list[Charger]) -> list[Charger]:
        return list(
            Charger.objects.filter(pk__in=[c.pk for c in chargers]).select_related(
                "location", "manager_node"
            )
        )

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
            return queryset.filter(connector_id__isnull=False)
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
            raise CommandError("--cp requires a connector identifier (A, B, ...).")

        return connector, None

    def _resolve_default_base_charger(
        self, queryset: QuerySet[Charger]
    ) -> Charger | None:
        """Return a default base charger for singular ``charger`` command usage."""

        base = queryset.filter(connector_id__isnull=True).order_by("charger_id").first()
        if base:
            return base
        return queryset.order_by("charger_id", "connector_id").first()

    def _select_aggregate_charger(self, chargers: list[Charger]) -> Charger | None:
        """Return the aggregate charger when all rows belong to one station."""

        if len(chargers) <= 1:
            return None
        serials = {item.charger_id for item in chargers}
        if len(serials) != 1:
            return None
        for item in chargers:
            if item.connector_id is None:
                return item
        return None

    def _resolve_aggregate_charger(self, chargers: list[Charger]) -> Charger | None:
        """Return an aggregate charger for single-station selections when available."""

        aggregate = self._select_aggregate_charger(chargers)
        if aggregate is not None:
            return aggregate
        serials = {item.charger_id for item in chargers}
        if len(serials) != 1:
            return None
        return Charger.objects.filter(
            charger_id=next(iter(serials)), connector_id__isnull=True
        ).first()

    def _rename_charger(
        self, charger: Charger, rename_value: str, *, interactive: bool
    ) -> Charger:
        """Rename ``charger`` and optionally rename its connectors."""

        new_name = (rename_value or "").strip()
        if not new_name:
            if interactive and not self.stdin.isatty():
                raise CommandError(
                    "--rename without a value requires an interactive terminal."
                )
            new_name = self._prompt_text(
                f"New display name for {charger.charger_id}",
                default=charger.display_name or charger.charger_id,
            )
        if not new_name:
            raise CommandError("A non-empty charger name is required.")

        charger.display_name = new_name
        charger.save(update_fields=["display_name"])

        if charger.connector_id is None:
            self._rename_connectors_for_base(charger, new_name, interactive=interactive)

        return charger

    def _rename_connectors_for_base(
        self, charger: Charger, station_name: str, *, interactive: bool
    ) -> None:
        """Rename connector display names for a base charger when requested."""

        connectors = list(
            Charger.objects.filter(
                charger_id=charger.charger_id, connector_id__isnull=False
            ).order_by("connector_id")
        )
        if not connectors:
            return

        prompt = f"Rename {len(connectors)} connector(s) to '{station_name} <letter>' automatically? [Y/n]: "
        auto_rename = True
        if interactive:
            auto_rename = self._stdin_confirm(prompt=prompt, default=True)
        if auto_rename:
            for item in connectors:
                suffix = Charger.connector_letter_from_value(item.connector_id) or str(
                    item.connector_id
                )
                item.display_name = f"{station_name} {suffix}"
                item.save(update_fields=["display_name"])
            return

        for item in connectors:
            suffix = Charger.connector_letter_from_value(item.connector_id) or str(
                item.connector_id
            )
            default_name = item.display_name or f"{station_name} {suffix}"
            custom_name = self._prompt_text(
                f"Display name for connector {suffix}",
                default=default_name,
            )
            item.display_name = custom_name or default_name
            item.save(update_fields=["display_name"])

    def _send_stop(self, chargers: list[Charger]) -> int:
        """Send ``RemoteStopTransaction`` to each selected charger with an active session."""

        sent = 0
        for charger in chargers:
            tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
            if tx_obj is None:
                self.stderr.write(
                    self.style.ERROR(f"{charger}: no active transaction, skipping.")
                )
                continue
            self._send_control_call(
                charger,
                action="RemoteStopTransaction",
                payload={"transactionId": tx_obj.pk},
                metadata={"transaction_id": tx_obj.pk},
                timeout_message="RemoteStopTransaction request timed out",
            )
            sent += 1

        if sent == 0:
            raise CommandError("No active transactions found for selected charger(s).")

        return sent

    def _send_restart(self, chargers: list[Charger]) -> int:
        """Send ``Reset`` to each selected charger."""

        sent = 0
        for charger in chargers:
            self._send_control_call(
                charger,
                action="Reset",
                payload={"type": "Soft"},
                metadata={},
                timeout_message="Reset request timed out: charger did not respond",
            )
            sent += 1
        return sent

    def _send_control_call(
        self,
        charger: Charger,
        *,
        action: str,
        payload: dict[str, object],
        metadata: dict[str, object],
        timeout_message: str,
    ) -> None:
        """Send a control action to an online charger and register pending metadata."""

        ws = store.get_connection(charger.charger_id, charger.connector_id)
        if ws is None:
            raise CommandError(f"{charger}: charger is not connected")

        message_id = uuid.uuid4().hex
        msg = json.dumps([2, message_id, action, payload])
        try:
            async_to_sync(ws.send)(msg)
        except Exception as exc:  # pragma: no cover - transport error
            raise CommandError(
                f"{charger}: failed to send {action} ({message_id}): {exc}"
            ) from exc

        log_key = store.identity_key(charger.charger_id, charger.connector_id)
        store.register_pending_call(
            message_id,
            {
                "action": action,
                "charger_id": charger.charger_id,
                "connector_id": charger.connector_id,
                "log_key": log_key,
                "requested_at": timezone.now(),
                **metadata,
            },
        )
        store.schedule_call_timeout(
            message_id,
            action=action,
            log_key=log_key,
            message=timeout_message,
        )

    def _prompt_text(self, label: str, *, default: str = "") -> str:
        """Prompt for text input when a terminal is attached, otherwise return default."""

        if not self.stdin.isatty():
            return default
        suffix = f" [{default}]" if default else ""
        self.stdout.write(f"{label}{suffix}: ", ending="")
        response = self.stdin.readline().strip()
        return response or default

    def _stdin_confirm(self, *, prompt: str, default: bool) -> bool:
        """Return a boolean answer from the terminal or ``default`` when non-interactive."""

        if not self.stdin.isatty():
            return default
        self.stdout.write(prompt, ending="")
        answer = self.stdin.readline().strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes"}

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

    def _render_sessions(self, chargers: Iterable[Charger], limit: int) -> None:
        entries = self._collect_session_entries(chargers)
        if not entries:
            self.stdout.write("No session logs found.")
            return

        entries.sort(key=lambda item: item["timestamp"], reverse=True)
        selected = entries[:limit]
        total_count = len(entries)
        heading = "Recent sessions"
        if total_count > limit:
            heading += f" (showing {len(selected)} of {total_count})"
        self.stdout.write(self.style.MIGRATE_HEADING(heading))
        for entry in selected:
            charger = entry["charger"]
            connector_label = self._connector_descriptor(charger)
            label = charger.display_name or charger.charger_id
            timestamp = self._format_dt(entry["timestamp"]) or "-"
            tx_id = entry["tx_id"] or "-"
            self.stdout.write(
                f"{timestamp}  {label} ({connector_label})  tx={tx_id}  {entry['path']}"
            )

    def _collect_session_entries(
        self, chargers: Iterable[Charger]
    ) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for charger in chargers:
            for folder in self._session_folders_for_charger(charger):
                for path in folder.glob("*.json"):
                    if not path.is_file():
                        continue
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        continue
                    timestamp = datetime.fromtimestamp(
                        stat.st_mtime, tz=dt_timezone.utc
                    )
                    entries.append(
                        {
                            "timestamp": timezone.localtime(timestamp),
                            "charger": charger,
                            "tx_id": self._session_transaction_id(path.name),
                            "path": path,
                        }
                    )
        return entries

    def _session_folders_for_charger(self, charger: Charger) -> list[Path]:
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
            safe_name = self._safe_session_name(name)
            path = store.SESSION_DIR / safe_name
            if path.exists() and path.is_dir():
                folders.append(path)
        return folders

    @staticmethod
    def _safe_session_name(name: str) -> str:
        return re.sub(r"[^\w.-]", "_", name)

    @staticmethod
    def _session_transaction_id(filename: str) -> str | None:
        stem = filename.rsplit(".", 1)[0]
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
        return None

    def _transaction_prefetch(self) -> Prefetch:
        return Prefetch(
            "transactions",
            queryset=Transaction.objects.all().prefetch_related(
                Prefetch(
                    "meter_values",
                    queryset=(
                        MeterValue.objects.filter(energy__isnull=False).order_by(
                            "timestamp"
                        )
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
            if charger.connector_id is None and charger.charger_id in aggregate_sources:
                total = aggregate_totals.get(charger.charger_id, total)
            status_label = self._status_label(charger)
            rfid_value = "on" if charger.require_rfid else "off"
            if (
                charger.connector_id is not None
                and status_label.casefold() == "charging"
            ):
                tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
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
            self.stdout.write("  ".join(row[key].ljust(widths[key]) for key in headers))

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
                    (
                        "Yes"
                        if store.is_connected(charger.charger_id, charger.connector_id)
                        else "No"
                    ),
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
                vendor_info = json.dumps(
                    charger.last_status_vendor_info, indent=2, sort_keys=True
                )
                self.stdout.write("Vendor Info:")
                self.stdout.write(vendor_info)

            if charger.last_meter_values:
                self._render_last_meter_values(charger.last_meter_values)

    def _render_last_meter_values(self, payload: dict) -> None:
        self.stdout.write("Last Meter Values:")
        if not isinstance(payload, dict):
            self.stdout.write("  -")
            return

        self._render_meter_values_transaction(payload)

        meter_values = payload.get("meterValue")
        if not isinstance(meter_values, list) or not meter_values:
            self.stdout.write("  No meter values reported.")
            return

        total = len(meter_values)
        for idx, entry in enumerate(meter_values, start=1):
            self._render_meter_value_entry(entry, idx, total)

    def _render_meter_values_transaction(self, payload: dict) -> None:
        transaction_id = payload.get("transactionId")
        if transaction_id is not None:
            self.stdout.write(f"  Transaction ID: {transaction_id}")

    def _render_meter_value_entry(self, entry: object, index: int, total: int) -> None:
        if not isinstance(entry, dict):
            return
        timestamp = entry.get("timestamp")
        if timestamp:
            label = "Timestamp" if total <= 1 else f"Timestamp {index}"
            self.stdout.write(f"  {label}: {timestamp}")

        sampled_values = entry.get("sampledValue")
        if not isinstance(sampled_values, list):
            return
        for sample in sampled_values:
            self._render_sampled_value(sample)

    def _render_sampled_value(self, sample: object) -> None:
        if not isinstance(sample, dict):
            return
        measurand = sample.get("measurand") or "Value"
        value_text = self._format_sample_value(sample.get("value"), sample.get("unit"))
        meta_text = self._format_sample_meta(
            sample.get("context"), sample.get("location")
        )
        self.stdout.write(f"  - {measurand}: {value_text}{meta_text}")

    @staticmethod
    def _format_sample_value(value: object, unit: object) -> str:
        value_parts: list[str] = []
        if value is not None:
            value_parts.append(str(value))
        if unit:
            value_parts.append(str(unit))
        return " ".join(value_parts) if value_parts else "-"

    @staticmethod
    def _format_sample_meta(context: object, location: object) -> str:
        meta_parts: list[str] = []
        if context:
            meta_parts.append(f"context: {context}")
        if location:
            meta_parts.append(f"location: {location}")
        return f" ({', '.join(meta_parts)})" if meta_parts else ""

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

    def _upsert_ws_auth_user(self, *, username: str, password: str):
        """Create or update the websocket HTTP Basic user for charger protection."""

        user_model = get_user_model()
        create_defaults: dict[str, object] = {}
        if hasattr(user_model, "is_active"):
            create_defaults["is_active"] = True

        user, created = user_model.objects.get_or_create(
            username=username,
            defaults=create_defaults,
        )

        user.set_password(password)
        update_fields = ["password"]
        if hasattr(user, "is_active") and not user.is_active:
            user.is_active = True
            update_fields.append("is_active")
        user.save(update_fields=update_fields)
        return user

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
