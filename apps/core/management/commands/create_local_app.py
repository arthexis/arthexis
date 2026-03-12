"""Backward-compatible shim for the old create_local_app command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate to the unified ``create app`` command."""

    help = "Deprecated alias for `create app`; kept for compatibility."

    def add_arguments(self, parser):
        """Reuse arguments accepted by ``create app``."""

        parser.add_argument("name", help="App package name (lowercase snake_case).")
        parser.add_argument(
            "--backend-only",
            action="store_true",
            help="Create an app scaffold without views.py, urls.py, and routes.py.",
        )
        parser.add_argument("--apps-dir", dest="apps_dir", help="Override apps directory path.")

    def handle(self, *args, **options):
        """Delegate to the new one-word create command."""

        command_args = ["app", options["name"]]
        if options.get("backend_only"):
            command_args.append("--backend-only")
        if options.get("apps_dir"):
            command_args.extend(["--apps-dir", options["apps_dir"]])
        call_command("create", *command_args, stdout=self.stdout)
