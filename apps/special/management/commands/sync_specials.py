"""Sync DB definitions for declared special management commands."""

from django.core.management import get_commands, load_command_class
from django.core.management.base import BaseCommand, CommandError

from apps.special.registry import sync_special_command


class Command(BaseCommand):
    """Register or refresh command metadata for all declared special commands."""

    help = "Sync @special_command declarations into apps.special DB tables."

    def add_arguments(self, parser) -> None:
        """Wire command arguments."""

        parser.add_argument(
            "--command",
            action="append",
            dest="commands",
            default=[],
            help="Optional command name(s) to sync; defaults to all declared specials.",
        )

    def handle(self, *args, **options) -> None:
        """Sync one or more declared commands into the DB registry."""

        selected = [name.strip() for name in options.get("commands", []) if name.strip()]
        command_map = get_commands()

        synced = 0
        for command_name, app_name in sorted(command_map.items()):
            if selected and command_name not in selected:
                continue

            command_instance = load_command_class(app_name, command_name)
            command_cls = command_instance.__class__
            if not hasattr(command_cls, "special_command"):
                continue

            special = sync_special_command(command_name=command_name, command_cls=command_cls)
            synced += 1
            self.stdout.write(
                self.style.SUCCESS(f"Synced {command_name} -> {special.name}/{special.plural_name}")
            )

        if selected and not synced:
            raise CommandError("No selected commands were declared with @special_command.")

        self.stdout.write(self.style.SUCCESS(f"Synced {synced} special command definition(s)."))
