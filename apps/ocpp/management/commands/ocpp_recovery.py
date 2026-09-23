"""Operator-facing charger/session recovery command."""

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.domain.sessions import clear_stale_charger_state
from apps.ocpp.domain.snapshots import snapshot_charger
from apps.ocpp.models import Charger


class Command(BaseCommand):
    help = "Inspect or clear stale derived OCPP charger/session state."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--charger", required=True, help="Charger identity.")
        parser.add_argument(
            "--clear-stale-state",
            action="store_true",
            help="Remove stale live sessions from the charger's current derived state.",
        )
        parser.add_argument(
            "--reason",
            default="",
            help="Optional operator reason retained with the cleared session state.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit the charger recovery snapshot as JSON.",
        )

    def handle(self, *args, **options):
        identity = options["charger"]
        try:
            charger = (
                Charger.objects.select_related("station_model", "connection")
                .prefetch_related("connectors", "transactions")
                .get(identity=identity)
            )
        except Charger.DoesNotExist as error:
            raise CommandError(f"Unknown charger: {identity}") from error

        cleared_ids: tuple[int, ...] = ()
        if options["clear_stale_state"]:
            cleared_ids = clear_stale_charger_state(
                charger,
                reason=options["reason"],
            )
            charger = (
                Charger.objects.select_related("station_model", "connection")
                .prefetch_related("connectors", "transactions")
                .get(pk=charger.pk)
            )

        snapshot = snapshot_charger(charger)
        if options["json_output"]:
            payload = _serialize(asdict(snapshot))
            payload["cleared_session_count"] = len(cleared_ids)
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        self.stdout.write(f"Charger: {snapshot.identity}")
        self.stdout.write(f"State: {snapshot.state}")
        self.stdout.write(f"Connection: {snapshot.connection_state}")
        if snapshot.connection_last_seen_at:
            self.stdout.write(
                f"Last seen: {snapshot.connection_last_seen_at.isoformat()}"
            )
        if snapshot.connection_lease_expires_at:
            self.stdout.write(
                f"Presence lease expires: {snapshot.connection_lease_expires_at.isoformat()}"
            )
        self.stdout.write(
            "Connectors: "
            + (", ".join(snapshot.connector_states) if snapshot.connector_states else "none")
        )
        self.stdout.write(f"Active sessions: {snapshot.active_transactions}")
        self.stdout.write(f"Unresolved sessions: {snapshot.unresolved_sessions}")
        self.stdout.write(f"Operator-cleared sessions: {snapshot.cleared_sessions}")
        self.stdout.write(f"Why: {snapshot.state_reason}")
        if snapshot.waiting_for:
            self.stdout.write(f"Waiting for: {snapshot.waiting_for}")

        if snapshot.current_transaction_id:
            self.stdout.write(
                f"Current session: {snapshot.current_transaction_id}"
            )
            if snapshot.current_transaction_started:
                self.stdout.write(
                    "Session started: "
                    f"{snapshot.current_transaction_started.isoformat()}"
                )
            if snapshot.current_transaction_last_activity:
                self.stdout.write(
                    "Session last evidence: "
                    f"{snapshot.current_transaction_last_activity.isoformat()}"
                )
        elif snapshot.latest_unresolved_transaction_id:
            self.stdout.write(
                "Unresolved session: "
                f"{snapshot.latest_unresolved_transaction_id}"
            )
            if snapshot.latest_unresolved_activity:
                self.stdout.write(
                    "Unresolved last evidence: "
                    f"{snapshot.latest_unresolved_activity.isoformat()}"
                )
        if snapshot.last_cleared_transaction_id:
            self.stdout.write(
                "Last cleared session: "
                f"{snapshot.last_cleared_transaction_id}"
            )
            if snapshot.last_recovery_cleared_at:
                self.stdout.write(
                    "Cleared at: "
                    f"{snapshot.last_recovery_cleared_at.isoformat()}"
                )
            if snapshot.last_recovery_clear_reason:
                self.stdout.write(
                    "Reason: "
                    f"{snapshot.last_recovery_clear_reason}"
                )

        if options["clear_stale_state"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cleared {len(cleared_ids)} stale live session(s) from current state."
                )
            )


def _serialize(value):
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
