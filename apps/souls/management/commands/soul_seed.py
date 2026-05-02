from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.souls.models import CardSession
from apps.souls.services import (
    activate_soul_seed_card,
    compose_skill_bundle,
    evict_stale_card_sessions,
    provision_soul_seed_card,
)


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
        activate_parser = subparsers.add_parser(
            "activate",
            help="Activate or close a provisioned Soul Seed card at a suite console.",
        )
        activate_parser.add_argument("--card-uid", help="RFID card UID to activate.")
        activate_parser.add_argument(
            "--scan-json",
            help="Path or inline JSON scanner payload containing card_uid, uid, or rfid.",
        )
        activate_parser.add_argument(
            "--console-id",
            required=True,
            help="Stable console identity for session isolation.",
        )
        activate_parser.add_argument("--reader-id", default="", help="Reader identity, when known.")
        activate_parser.add_argument(
            "--trust-tier",
            default=CardSession.TrustTier.UNKNOWN,
            choices=CardSession.TrustTier.values,
            help="Trust tier assigned to this activation source.",
        )
        activate_parser.add_argument(
            "--timeout-seconds",
            type=int,
            default=0,
            help="Evict this console's active sessions older than this many seconds before activation.",
        )
        activate_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the activation payload as JSON.",
        )
        stale_parser = subparsers.add_parser(
            "evict-stale",
            help="Evict stale active Soul Seed sessions for a console.",
        )
        stale_parser.add_argument("--console-id", required=True, help="Stable console identity.")
        stale_parser.add_argument(
            "--timeout-seconds",
            type=int,
            required=True,
            help="Evict active sessions older than this many seconds.",
        )
        stale_parser.add_argument("--reason", default="timeout", help="Eviction reason.")
        stale_parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")

    def handle(self, *args, **options):
        if options["action"] == "activate":
            return self._handle_activate(options)
        if options["action"] == "evict-stale":
            return self._handle_evict_stale(options)
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

    def _handle_activate(self, options):
        try:
            summary = activate_soul_seed_card(
                self._card_uid_from_options(options),
                console_id=options.get("console_id") or "",
                reader_id=options.get("reader_id") or "",
                trust_tier=options.get("trust_tier") or CardSession.TrustTier.UNKNOWN,
                timeout_seconds=options.get("timeout_seconds") or None,
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        if options.get("json"):
            self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
            return
        session = summary["session"]
        self.stdout.write(
            f"Soul Seed card {summary['action']}: {summary.get('card', {}).get('card_uid', '')}"
        )
        self.stdout.write(f"Session: {session['session_id']} ({session['state']})")
        if session.get("runtime_namespace"):
            self.stdout.write(f"Runtime namespace: {session['runtime_namespace']}")
        if summary.get("interface", {}).get("suggestions"):
            self.stdout.write("Suggestions:")
            for suggestion in summary["interface"]["suggestions"]:
                self.stdout.write(f"- {suggestion}")

    def _handle_evict_stale(self, options):
        try:
            evicted = evict_stale_card_sessions(
                console_id=options.get("console_id") or "",
                timeout_seconds=options["timeout_seconds"],
                reason=options.get("reason") or "timeout",
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        summary = {"action": "evict-stale", "evicted_sessions": evicted}
        if options.get("json"):
            self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
            return
        self.stdout.write(f"Evicted stale Soul Seed sessions: {evicted}")

    def _card_uid_from_options(self, options) -> str:
        card_uid = (options.get("card_uid") or "").strip()
        scan_json = (options.get("scan_json") or "").strip()
        if card_uid:
            return card_uid
        if not scan_json:
            raise ValueError("--card-uid or --scan-json is required")
        try:
            if scan_json.lstrip().startswith("{"):
                payload = json.loads(scan_json)
            else:
                scan_path = Path(scan_json)
                if not scan_path.exists():
                    payload = json.loads(scan_json)
                else:
                    payload = json.loads(scan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Unable to read scanner JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("Scanner JSON must be an object.")
        for key in ("card_uid", "uid", "rfid", "card", "tag"):
            value = payload.get(key)
            if value:
                return str(value)
        raise ValueError("Scanner JSON must include card_uid, uid, or rfid.")
