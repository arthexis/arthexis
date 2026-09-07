"""List local apps and provide app-scoped utility operations."""

from __future__ import annotations

from django.apps import apps as django_apps
from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Inspect installed Django apps and run app-scoped helper operations."""

    help = (
        "List available apps by default. Use --show-flags and --show-commands to inspect "
        "app-specific options, or --reload-migrations with --yes for app-scoped migration "
        "reload workflows."
    )

    def add_arguments(self, parser) -> None:
        """Register command flags."""

        parser.add_argument("--app", help="Filter output to one app label.")
        parser.add_argument(
            "--show-flags",
            action="store_true",
            help="Show this command's app-specific flag reference.",
        )
        parser.add_argument(
            "--show-commands",
            action="store_true",
            help="List registered Django management commands for each selected app.",
        )
        parser.add_argument(
            "--reload-migrations",
            action="store_true",
            help="Reload migrations for --app, then restore full migration graph.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Required confirmation for destructive operations like --reload-migrations.",
        )

    def handle(self, *args, **options) -> None:
        """Run listing mode or app-specific operations."""

        app_label = (options.get("app") or "").strip()
        show_flags = bool(options.get("show_flags"))
        show_commands = bool(options.get("show_commands"))
        reload_migrations = bool(options.get("reload_migrations"))
        assume_yes = bool(options.get("yes"))

        if app_label:
            try:
                django_apps.get_app_config(app_label)
            except LookupError as error:
                raise CommandError(f"Unknown app label: {app_label}") from error

        if reload_migrations:
            if not app_label:
                raise CommandError("--reload-migrations requires --app.")
            if not assume_yes:
                raise CommandError("--reload-migrations requires --yes confirmation.")
            self._reload_migrations(app_label)
            return

        if app_label:
            app_configs = [django_apps.get_app_config(app_label)]
        else:
            app_configs = sorted(django_apps.get_app_configs(), key=lambda cfg: cfg.label)

        for config in app_configs:
            self.stdout.write(f"- {config.label} ({config.name})")
            if show_flags:
                self._write_flags()
            if show_commands:
                self._write_commands(config.label)

    def _reload_migrations(self, app_label: str) -> None:
        """Reset one app to zero, then migrate globally to restore dependency graph."""

        self.stdout.write(
            self.style.WARNING(
                f"Reloading migrations for '{app_label}' (migrate {app_label} zero, then migrate)."
            )
        )
        call_command("migrate", app_label, "zero")
        call_command("migrate")
        self.stdout.write(self.style.SUCCESS(f"Reloaded migrations for '{app_label}'."))

    def _write_flags(self) -> None:
        """Emit flag reference for the apps special command."""

        self.stdout.write("  flags:")
        self.stdout.write("    --app <label>            target a single app")
        self.stdout.write("    --show-flags             print this flag reference")
        self.stdout.write("    --show-commands          list management commands by app")
        self.stdout.write("    --reload-migrations      run migrate <app> zero then migrate")
        self.stdout.write("    --yes                    required confirmation for destructive actions")

    def _write_commands(self, app_label: str) -> None:
        """List Django management commands that belong to a selected app label."""

        target_name = django_apps.get_app_config(app_label).name
        commands = sorted(
            name
            for name, app_name in get_commands().items()
            if app_name and (app_name == target_name or app_name.split(".")[-1] == app_label)
        )
        self.stdout.write("  commands:")
        if not commands:
            self.stdout.write("    (none)")
            return
        for command in commands:
            self.stdout.write(f"    - {command}")
