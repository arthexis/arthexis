from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.souls.services import compose_skill_bundle, provision_soul_seed_card


class Command(BaseCommand):
    help = "Compose Soul Seed card intent, skill bundle, and interface planning data."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        compose_parser = subparsers.add_parser("compose", help="Compose a bundle plan from an operator prompt.")
        compose_parser.add_argument("--prompt", required=True, help="Problem or job description for this card.")
        compose_parser.add_argument("--limit", type=int, default=5, help="Maximum skill matches to include.")
        compose_parser.add_argument(
            "--write",
            action="store_true",
            help="Persist the intent, bundle, and interface spec. Defaults to dry-run.",
        )
        provision_parser = subparsers.add_parser(
            "provision",
            help="Provision Agent Card v1 payloads from an operator prompt and card UID.",
        )
        provision_parser.add_argument("--prompt", required=True, help="Problem or job description for this card.")
        provision_parser.add_argument("--card-uid", required=True, help="RFID card UID to bind to the payload.")
        provision_parser.add_argument("--limit", type=int, default=5, help="Maximum skill matches to include.")
        provision_parser.add_argument(
            "--write",
            action="store_true",
            help="Persist the intent, bundle, interface spec, RFID, and Soul Seed card.",
        )
        provision_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the full provisioning payload as JSON.",
        )
        provision_parser.add_argument(
            "--sectors-json-out",
            help="Write generated raw sector records to this JSON file for a future card writer.",
        )

    def handle(self, *args, **options):
        if options["action"] == "provision":
            return self._handle_provision(options)
        if options["action"] != "compose":
            raise CommandError(f"Unsupported soul_seed action: {options['action']}")
        prompt = (options.get("prompt") or "").strip()
        if not prompt:
            raise CommandError("--prompt is required")
        try:
            summary = compose_skill_bundle(
                prompt,
                created_by=getattr(self, "user", None),
                limit=options["limit"],
                dry_run=not options["write"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))

    def _handle_provision(self, options):
        prompt = (options.get("prompt") or "").strip()
        if not prompt:
            raise CommandError("--prompt is required")
        try:
            summary = provision_soul_seed_card(
                prompt,
                card_uid=options.get("card_uid") or "",
                created_by=getattr(self, "user", None),
                limit=options["limit"],
                dry_run=not options["write"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        sectors_json_out = options.get("sectors_json_out")
        if sectors_json_out:
            try:
                Path(sectors_json_out).write_text(
                    json.dumps(summary["sector_records"], indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as error:
                raise CommandError(str(error)) from error
        if options.get("json"):
            self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
            return
        write_state = "persisted" if options["write"] else "planned"
        self.stdout.write(f"Soul Seed card {write_state}: {summary['card']['card_uid']}")
        self.stdout.write(f"Fingerprint: {summary['card']['manifest_fingerprint']}")
        if summary["compatibility_notes"]:
            self.stdout.write("Compatibility notes:")
            for note in summary["compatibility_notes"]:
                self.stdout.write(f"- {note}")
