from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.cards.models import RFID as CoreRFID
from apps.ocpp import store
from apps.ocpp.models import Charger, MeterValue, Transaction
from apps.ocpp.views import _aggregate_dashboard_state
from apps.special.registry import special_command

from .chargers_cmd.actions import ChargersActionRunner
from .chargers_cmd.args import build_chargers_parser
from .chargers_cmd.legacy import resolve_action
from .chargers_cmd.render import ChargersRenderer


@special_command(singular="charger", plural="chargers", keystone_model="ocpp.Charger")
class Command(BaseCommand):
    help = "Inspect configured OCPP chargers and update their RFID settings."

    def add_arguments(self, parser) -> None:  # pragma: no cover - simple wiring
        """Register selectors, legacy flags, and verb-style subcommands."""

        build_chargers_parser(parser)

    def handle(self, *args, **options):
        """Dispatch charger command actions using verb-style subcommands or legacy flags."""

        self.renderer = ChargersRenderer(self)
        self.action_runner = ChargersActionRunner(self)
        action = resolve_action(options)
        queryset, selection = self._select_chargers(options)
        chargers = list(queryset.order_by('charger_id', 'connector_id'))

        if not chargers:
            self.stdout.write('No chargers found.')
            return

        self.action_runner.execute(
            action=action,
            chargers=chargers,
            queryset=queryset,
            selection=selection,
        )

    def _select_chargers(
        self, options: dict[str, object]
    ) -> tuple[QuerySet[Charger], dict[str, object]]:
        """Return the selected charger queryset and normalized selector metadata."""

        serial = options.get('serial')
        cp_raw = options.get('cp')
        use_default_base = bool(options.get('default_base'))
        queryset = (
            Charger.objects.all()
            .select_related('location', 'manager_node')
            .prefetch_related(self._transaction_prefetch())
        )

        if use_default_base and not serial and not cp_raw:
            default_charger = self._resolve_default_base_charger(queryset)
            if not default_charger:
                return queryset.none(), {
                    'serial': serial,
                    'cp_raw': cp_raw,
                    'has_selector': False,
                }
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
                        'Multiple chargers matched the provided connector id; showing all matches.'
                    )
                )

        if cp_path:
            queryset = self._filter_by_cp_path(queryset, cp_path)
            if not queryset.exists():
                raise CommandError(
                    f"No chargers found matching charge point path '{cp_path}'."
                )

        return queryset, {
            'serial': serial,
            'cp_raw': cp_raw,
            'connector_filter': connector_filter,
            'cp_path': cp_path,
            'has_selector': bool(serial)
            or connector_filter is not None
            or bool(cp_path),
        }

    def _require_selector(
        self,
        selection: dict[str, object],
        *,
        message: str = (
            'This action requires selecting at least one charger with --sn '
            'and/or --cp.'
        ),
    ) -> None:
        """Require an explicit charger selector for mutating or focused actions."""

        if not selection['has_selector']:
            raise CommandError(message)

    def _validate_positive_count(self, value: object, option_name: str) -> int:
        """Return a validated positive count for tail and sessions actions."""

        count = int(value)
        if count <= 0:
            raise CommandError(f'{option_name} requires a positive number.')
        return count

    def _handle_rfid_action(
        self,
        *,
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        mode: str,
    ) -> None:
        """Apply one RFID action to the selected chargers."""

        if mode in {'on', 'off'}:
            new_value = mode == 'on'
            updated = queryset.update(require_rfid=new_value)
            verb = 'Enabled' if new_value else 'Disabled'
            self.stdout.write(
                self.style.SUCCESS(
                    f'{verb} RFID authentication on {updated} charger(s).'
                )
            )
            return
        if mode == 'push':
            sent = self._send_local_rfid_list(chargers)
            self.stdout.write(
                self.style.SUCCESS(f'Sent local RFID list to {sent} charger(s).')
            )
            return
        if mode == 'lock':
            updated = queryset.update(require_rfid=True)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Enabled RFID authentication on {updated} charger(s).'
                )
            )
            sent = self._send_local_rfid_list(self._reload_chargers(chargers))
            self.stdout.write(
                self.style.SUCCESS(f'Sent local RFID list to {sent} charger(s).')
            )
            return
        raise CommandError(f'Unknown RFID action: {mode}')

    def _handle_auth_set(
        self,
        *,
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        username: str,
        password: object,
    ) -> None:
        """Enable websocket auth on the selected chargers with one user."""

        username = username.strip()
        if not username:
            raise CommandError('--ws-auth-username is required.')
        if not password:
            raise CommandError('--ws-auth-password is required.')
        ws_auth_user = self._upsert_ws_auth_user(
            username=username,
            password=str(password),
        )
        updated = queryset.update(ws_auth_user=ws_auth_user, ws_auth_group=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Enabled websocket auth on {updated} charger(s) with user '{username}'."
            )
        )

    def _handle_rename(self, *, chargers: list[Charger], value: object) -> None:
        """Rename one selected charger, collapsing station selections to the base charger."""

        rename_targets = self._action_targets_for_single_station(chargers)
        if len(rename_targets) != 1:
            raise CommandError(
                '--rename requires selecting exactly one charger using --sn '
                'and/or --cp.'
            )
        rename_value = '' if value is None else str(value)
        renamed = self._rename_charger(
            rename_targets[0],
            rename_value,
            interactive=rename_value == '',
        )
        self.stdout.write(
            self.style.SUCCESS(f"Renamed charger to '{renamed.display_name}'.")
        )

    def _action_targets_for_single_station(self, chargers: list[Charger]) -> list[Charger]:
        """Return a collapsed base charger target for single-station actions when available."""

        aggregate_target = self._resolve_aggregate_charger(chargers)
        if aggregate_target is not None:
            return [aggregate_target]
        return chargers

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

    def _send_local_rfid_list(self, chargers: list[Charger]) -> int:
        """Send ``SendLocalList`` full updates using released RFID entries."""

        authorization_list = self._build_local_authorization_list()
        sent = 0
        for charger in chargers:
            list_version = (charger.local_auth_list_version or 0) + 1
            self._send_control_call(
                charger,
                action="SendLocalList",
                payload={
                    "listVersion": list_version,
                    "updateType": "Full",
                    "localAuthorizationList": [entry.copy() for entry in authorization_list],
                },
                metadata={"list_version": list_version, "list_size": len(authorization_list)},
                timeout_message="SendLocalList request timed out",
            )
            sent += 1
        return sent

    def _build_local_authorization_list(self) -> list[dict[str, object]]:
        """Return released RFID values encoded for OCPP ``SendLocalList`` payloads."""

        return [
            {
                "idTag": (CoreRFID.normalize_code(tag.rfid) or "")[:20],
                "idTagInfo": {"status": "Accepted"},
            }
            for tag in CoreRFID.objects.filter(released=True)
            .order_by("rfid")
            .only("rfid")
            .iterator()
        ]

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

    def _render_tail(self, charger: Charger, limit: int) -> None:
        self.renderer.render_tail(charger, limit)

    def _render_sessions(self, chargers: Iterable[Charger], limit: int) -> None:
        self.renderer.render_sessions(chargers, limit)

    def _render_table(self, chargers: Iterable[Charger]) -> None:
        self.renderer.render_table(chargers)

    def _render_details(self, chargers: Iterable[Charger]) -> None:
        self.renderer.render_details(chargers)

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
