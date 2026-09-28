"""Inspect durable OCPP compatibility evidence for field diagnostics."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.services.compatibility_diagnostics import (
    query_compatibility_diagnostics,
)


class Command(BaseCommand):
    help = "Inspect recent OCPP compatibility quirks and fallback evidence."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--charger",
            metavar="IDENTITY",
            help="Limit evidence to one charger identity.",
        )
        parser.add_argument(
            "--kind",
            action="append",
            default=[],
            help="Limit to one evidence kind; repeat for multiple kinds.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum recent evidence rows to display (default: 50).",
        )
        parser.add_argument("--json", action="store_true", dest="json_output")

    def handle(self, *args, **options) -> None:
        try:
            payload = query_compatibility_diagnostics(
                charger_identity=options["charger"],
                kinds=options["kind"],
                limit=options["limit"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error

        if options["json_output"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        charger = payload["charger"] or "all"
        self.stdout.write(f"charger: {charger}")
        self.stdout.write(f"compatibility_events: {payload['total']}")
        counts = payload["counts"]
        if counts:
            rendered_counts = ", ".join(
                f"{kind}={count}" for kind, count in counts.items()
            )
            self.stdout.write(f"kinds: {rendered_counts}")
        else:
            self.stdout.write("kinds: none")
        self.stdout.write(
            f"latest_observed_at: {payload['latest_observed_at'] or '-'}"
        )

        for event in payload["events"]:
            details = json.dumps(
                event["details"],
                sort_keys=True,
                separators=(",", ":"),
            )
            context = [
                event["observed_at"],
                event["kind"],
                f"charger={event['charger'] or '-'}",
                f"protocol={event['protocol'] or '-'}",
            ]
            if event["action"]:
                context.append(f"action={event['action']}")
            if event["unique_id"]:
                context.append(f"id={event['unique_id']}")
            self.stdout.write(" | ".join(context) + f" | {details}")
