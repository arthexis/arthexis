from django.core.management.base import BaseCommand, CommandError

from rfid.reader import RC522Reader


class Command(BaseCommand):
    """Read a tag using the RC522 reader."""

    help = "Read an RFID tag using the RC522 module"

    def handle(self, *args, **options):  # pragma: no cover - hardware interaction
        try:
            reader = RC522Reader()
        except RuntimeError as exc:
            raise CommandError(str(exc))
        self.stdout.write("Place your RFID tag near the reader...")
        tag_id, text = reader.read()
        self.stdout.write(f"ID: {tag_id}\nText: {text}")
