from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.skills.hook_context import render_hooks_json
from apps.skills.models import Hook


class Command(BaseCommand):
    help = "List deterministic Codex hook commands enabled for this node."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list"])
        parser.add_argument("--event", choices=[choice.value for choice in Hook.Event])
        parser.add_argument(
            "--platform",
            choices=[choice.value for choice in Hook.Platform],
            help="Filter for a target platform. Defaults to the current OS.",
        )

    def handle(self, *args, **options):
        if options["action"] != "list":
            raise CommandError("Unsupported action")
        self.stdout.write(
            render_hooks_json(
                event=options.get("event"),
                platform=options.get("platform"),
            )
        )
