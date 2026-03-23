"""Lightweight stdin shortcut listener for server-side shortcut execution."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.shortcuts.models import Shortcut
from apps.shortcuts.runtime import ShortcutExecutionError, execute_server_shortcut


class Command(BaseCommand):
    help = "Listen for key-combo lines on stdin and execute active server shortcuts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Read a single key combo line and exit.",
        )

    def handle(self, *args, **options):
        once = bool(options.get("once"))
        self.stdout.write("Shortcut listener ready. Enter key combos like CTRL+SHIFT+K")
        while True:
            try:
                line = input().strip()
            except EOFError:
                break
            if not line:
                if once:
                    break
                continue
            combo = Shortcut.normalize_key_combo(line)
            shortcut = Shortcut.objects.filter(
                kind=Shortcut.Kind.SERVER,
                is_active=True,
                key_combo=combo,
            ).first()
            if shortcut is None:
                self.stdout.write(self.style.WARNING(f"No active server shortcut for {combo}"))
            else:
                try:
                    execution = execute_server_shortcut(shortcut=shortcut)
                except ShortcutExecutionError as exc:
                    self.stdout.write(self.style.ERROR(f"Failed {combo}: {exc}"))
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Executed {execution.target_identifier}: {execution.action_result.value}"
                        )
                    )
            if once:
                break
