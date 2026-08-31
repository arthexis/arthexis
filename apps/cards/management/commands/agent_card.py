from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.cards.agent_card import AgentCardError, parse_agent_card


class Command(BaseCommand):
    help = "Inspect deterministic Agent Card v1 sector payloads."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        inspect_parser = subparsers.add_parser("inspect", help="Parse and validate Agent Card v1 payloads.")
        inspect_parser.add_argument(
            "--sectors-json",
            help="Path to a JSON object keyed by sector or an array of sectors 1-15.",
        )
        inspect_parser.add_argument(
            "--record",
            action="append",
            default=[],
            help="One 48-byte sector record. Provide 15 records for sectors 1-15.",
        )
        inspect_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    def handle(self, *args, **options):
        if options["action"] != "inspect":
            raise CommandError(f"Unsupported agent_card action: {options['action']}")
        sectors = self._load_sector_payloads(options)
        try:
            card = parse_agent_card(sectors)
        except AgentCardError as error:
            raise CommandError(str(error)) from error
        payload = card.to_dict()
        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return
        self.stdout.write(f"Agent Card v1 fingerprint: {payload['fingerprint']}")
        self.stdout.write(f"Identity slots: {len(payload['identity_slots'])}")
        self.stdout.write(f"Capability slots: {len(payload['capability_slots'])}")
        self.stdout.write(f"File slots: {len(payload['file_slots'])}")

    def _load_sector_payloads(self, options):
        sectors_json = options.get("sectors_json")
        records = options.get("record") or []
        if sectors_json and records:
            raise CommandError("Use either --sectors-json or --record, not both.")
        if sectors_json:
            try:
                return json.loads(Path(sectors_json).read_text(encoding="utf-8"))
            except OSError as error:
                raise CommandError(str(error)) from error
            except json.JSONDecodeError as error:
                raise CommandError(f"Invalid sector JSON: {error}") from error
        if records:
            return records
        raise CommandError("Provide --sectors-json or 15 --record values.")
