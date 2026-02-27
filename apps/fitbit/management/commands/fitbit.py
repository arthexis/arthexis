"""CLI for configuring Fitbit integration, storing query results, and testing message flow."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.fitbit.models import FitbitConnection, FitbitHealthSample
from apps.fitbit.services import (
    FitbitPayloadError,
    dispatch_net_messages_to_connections,
    record_health_payload,
)
from apps.nodes.models import NetMessage


class Command(BaseCommand):
    """Manage Fitbit configuration, health polling snapshots, and Net Message tests."""

    help = "Configure Fitbit accounts, store polled payloads, and test fitbit-targeted Net Messages."

    def add_arguments(self, parser):
        """Define CLI arguments for all fitbit command actions."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        configure = subparsers.add_parser("configure", help="Create or update a Fitbit connection.")
        configure.add_argument("name", help="Local label for this Fitbit connection.")
        configure.add_argument("--user-id", required=True, help="Fitbit user id.")
        configure.add_argument("--access-token", required=True, help="Fitbit API access token.")
        configure.add_argument("--refresh-token", default="", help="Fitbit API refresh token.")
        configure.add_argument("--device-id", default="", help="Optional Fitbit device id.")
        configure.add_argument("--token-expires-minutes", type=int, default=60)
        configure.add_argument("--inactive", action="store_true", help="Save connection as inactive.")

        query = subparsers.add_parser("query", help="Store or list Fitbit polled health payloads.")
        query.add_argument("name", help="Connection name.")
        query.add_argument("--resource", default="generic", help="Resource kind (steps, sleep, heart, etc).")
        query.add_argument("--json", dest="inline_json", help="Inline JSON payload to store.")
        query.add_argument("--from-file", dest="from_file", help="Path to JSON payload file.")
        query.add_argument("--list", action="store_true", help="List latest stored samples.")
        query.add_argument("--limit", type=int, default=5, help="List limit.")

        net_test = subparsers.add_parser("net-test", help="Send a test Net Message to Fitbit targets.")
        net_test.add_argument("name", help="Connection name.")
        net_test.add_argument("--subject", default="Fitbit Test")
        net_test.add_argument("--body", default="Testing Fitbit Net Message route")

        drain = subparsers.add_parser("drain", help="Dispatch fitbit-targeted Net Messages.")
        drain.add_argument("--name", help="Optional connection name filter.")
        drain.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options):
        """Route command actions to dedicated handlers."""
        action = options["action"]
        if action == "configure":
            self._handle_configure(options)
            return
        if action == "query":
            self._handle_query(options)
            return
        if action == "net-test":
            self._handle_net_test(options)
            return
        if action == "drain":
            self._handle_drain(options)
            return
        raise CommandError(f"Unsupported action '{action}'.")

    def _handle_configure(self, options: dict[str, object]) -> None:
        """Create or update a Fitbit connection using provided credentials."""
        name = str(options["name"]).strip()
        if not name:
            raise CommandError("name is required")

        token_minutes = int(options.get("token_expires_minutes") or 60)
        if token_minutes <= 0:
            raise CommandError("--token-expires-minutes must be greater than zero")

        connection, _created = FitbitConnection.objects.update_or_create(
            name=name,
            defaults={
                "fitbit_user_id": str(options["user_id"]).strip(),
                "access_token": str(options["access_token"]),
                "refresh_token": str(options.get("refresh_token") or ""),
                "device_id": str(options.get("device_id") or ""),
                "token_expires_at": timezone.now() + timedelta(minutes=token_minutes),
                "is_active": not bool(options.get("inactive")),
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Configured Fitbit connection '{connection.name}'."))

    def _handle_query(self, options: dict[str, object]) -> None:
        """Store a polled payload snapshot or list previously stored samples."""
        connection = self._get_connection(str(options["name"]))

        if options.get("list"):
            limit = int(options.get("limit") or 5)
            if limit <= 0:
                raise CommandError("--limit must be greater than zero")
            samples = FitbitHealthSample.objects.filter(connection=connection).order_by("-polled_at")[:limit]
            if not samples:
                self.stdout.write("No samples stored.")
                return
            for sample in samples:
                self.stdout.write(
                    f"{sample.polled_at.isoformat()} resource={sample.resource} payload={json.dumps(sample.payload, sort_keys=True)}"
                )
            return

        payload = self._load_payload(options)
        try:
            sample = record_health_payload(
                connection=connection,
                resource=str(options.get("resource") or "generic"),
                payload=payload,
            )
        except FitbitPayloadError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Stored Fitbit sample id={sample.pk} for {connection.name}."))

    def _handle_net_test(self, options: dict[str, object]) -> None:
        """Broadcast and dispatch a fitbit-targeted Net Message for one connection."""
        connection = self._get_connection(str(options["name"]))
        message = NetMessage.objects.create(
            subject=str(options.get("subject") or "Fitbit Test")[:64],
            body=str(options.get("body") or "")[:256],
            lcd_channel_type="fitbit",
        )
        deliveries = dispatch_net_messages_to_connections(connection=connection, limit=1)
        self.stdout.write(
            self.style.SUCCESS(
                f"Created Net Message {message.pk} and generated {len(deliveries)} Fitbit delivery record(s)."
            )
        )

    def _handle_drain(self, options: dict[str, object]) -> None:
        """Dispatch fitbit-targeted Net Messages to active Fitbit connections."""
        name = options.get("name")
        connection = self._get_connection(str(name)) if name else None
        deliveries = dispatch_net_messages_to_connections(
            connection=connection,
            limit=int(options.get("limit") or 25),
        )
        self.stdout.write(self.style.SUCCESS(f"Created {len(deliveries)} Fitbit delivery record(s)."))

    def _get_connection(self, name: str) -> FitbitConnection:
        """Resolve a Fitbit connection by name or raise a command error."""
        connection = FitbitConnection.objects.filter(name=name.strip()).first()
        if connection is None:
            raise CommandError(f"Fitbit connection '{name}' was not found.")
        return connection

    def _load_payload(self, options: dict[str, object]) -> dict[str, object]:
        """Resolve payload content from CLI flags and parse it as JSON object."""
        inline_json = options.get("inline_json")
        from_file = options.get("from_file")

        if inline_json and from_file:
            raise CommandError("Use only one of --json or --from-file.")

        source = "{}"
        if inline_json:
            source = str(inline_json)
        elif from_file:
            payload_path = Path(str(from_file))
            try:
                source = payload_path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise CommandError(f"Payload file '{payload_path}' was not found.") from exc
            except OSError as exc:
                raise CommandError(f"Could not read payload file '{payload_path}': {exc}") from exc

        try:
            payload = json.loads(source)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            raise CommandError("Payload must deserialize into a JSON object.")
        return payload
