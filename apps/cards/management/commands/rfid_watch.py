from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Toggle the always-on RFID watcher"

    def add_arguments(self, parser):
        parser.add_argument(
            "--stop",
            action="store_true",
            help="Stop the always-on watcher instead of starting it",
        )

    def handle(self, *args, **options):
        from apps.cards.always_on import is_running, start, stop

        if options["stop"]:
            stop()
            self.stdout.write(self.style.SUCCESS("RFID watch disabled"))
        else:
            start()
            state = "enabled" if is_running() else "disabled"
            self.stdout.write(self.style.SUCCESS(f"RFID watch {state}"))
