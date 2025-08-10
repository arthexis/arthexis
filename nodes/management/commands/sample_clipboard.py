from django.core.management.base import BaseCommand

import pyperclip
from pyperclip import PyperclipException

from nodes.models import TextSample


class Command(BaseCommand):
    help = "Save current clipboard contents to a TextSample entry"

    def handle(self, *args, **options):
        try:
            content = pyperclip.paste()
        except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
            self.stderr.write(f"Clipboard error: {exc}")
            return
        if not content:
            self.stdout.write("Clipboard is empty")
            return
        if TextSample.objects.filter(content=content).exists():
            self.stdout.write("Duplicate sample not created")
            return
        sample = TextSample.objects.create(content=content)
        self.stdout.write(self.style.SUCCESS(f"Saved sample at {sample.created_at}"))
