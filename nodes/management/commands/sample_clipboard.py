from django.core.management.base import BaseCommand

import pyperclip
from pyperclip import PyperclipException

from nodes.models import ContentSample, Node


class Command(BaseCommand):
    help = "Save current clipboard contents to a ContentSample entry"

    def handle(self, *args, **options):
        try:
            content = pyperclip.paste()
        except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
            self.stderr.write(f"Clipboard error: {exc}")
            return
        if not content:
            self.stdout.write("Clipboard is empty")
            return
        if ContentSample.objects.filter(
            content=content, kind=ContentSample.TEXT
        ).exists():
            self.stdout.write("Duplicate sample not created")
            return
        node = Node.get_local()
        sample = ContentSample.objects.create(
            content=content, node=node, kind=ContentSample.TEXT
        )
        self.stdout.write(self.style.SUCCESS(f"Saved sample at {sample.created_at}"))
