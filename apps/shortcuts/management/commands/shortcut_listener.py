"""Lightweight stdin shortcut listener for server-side shortcut execution."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.shortcuts.models import Shortcut


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
            ).select_related("recipe").first()
            if shortcut is None or shortcut.recipe is None:
                self.stdout.write(self.style.WARNING(f"No active server shortcut for {combo}"))
            else:
                execution = shortcut.recipe.execute(shortcut_key=combo)
                self.stdout.write(self.style.SUCCESS(f"Executed {shortcut.recipe.slug}: {execution.result}"))
            if once:
                break
