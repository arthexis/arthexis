from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.pyxel.models import PyxelUnavailableError, PyxelViewport


class Command(BaseCommand):
    help = "Open a Pyxel viewport window using its slug or name."

    def add_arguments(self, parser):
        parser.add_argument("viewport", help="Pyxel viewport slug or name")

    def handle(self, *args, **options):
        identifier = options["viewport"]
        viewport = PyxelViewport.objects.filter(slug=identifier).first()
        if viewport is None:
            viewport = PyxelViewport.objects.filter(name=identifier).first()
        if viewport is None:
            raise CommandError(f"Viewport '{identifier}' was not found")

        self.stdout.write(f"Opening viewport '{viewport.name}' ({viewport.slug})")
        try:
            viewport.open_viewport()
        except PyxelUnavailableError as exc:
            raise CommandError(str(exc)) from exc
