from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.souls.services import compose_skill_bundle


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

    def handle(self, *args, **options):
        if options["action"] != "compose":
            raise CommandError(f"Unsupported soul_seed action: {options['action']}")
        prompt = (options.get("prompt") or "").strip()
        if not prompt:
            raise CommandError("--prompt is required")
        summary = compose_skill_bundle(
            prompt,
            created_by=getattr(self, "user", None),
            limit=options["limit"],
            dry_run=not options["write"],
        )
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
