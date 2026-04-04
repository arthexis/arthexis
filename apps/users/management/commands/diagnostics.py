"""Diagnostics utilities for user-linked bug triage bundles."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.users.diagnostics import build_bundle_for_username


class Command(BaseCommand):
    help = "Build a user diagnostics bundle that correlates captured errors and feedback."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Username to build the bundle for.")
        parser.add_argument(
            "--title",
            default="",
            help="Optional bundle title override.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of recent events to include.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("Username is required.")
        limit = max(1, int(options["limit"]))
        try:
            bundle = build_bundle_for_username(
                username=username,
                title=options.get("title") or "",
                limit=limit,
            )
        except Exception as exc:
            raise CommandError(f"Unable to build diagnostics bundle: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Created diagnostics bundle #{bundle.pk} for {username} with {bundle.events.count()} event(s)."
            )
        )
