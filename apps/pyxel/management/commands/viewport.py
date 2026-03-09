from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from apps.core.ui import build_graphical_subprocess_env, has_graphical_display
from apps.pyxel.models import PyxelUnavailableError, PyxelViewport


class Command(BaseCommand):
    help = "Open a Pyxel viewport window using its slug or name."

    def add_arguments(self, parser):
        parser.add_argument("viewport", nargs="?", help="Pyxel viewport slug or name")

    def _resolve_viewport(self, identifier: str | None) -> PyxelViewport:
        """Resolve a viewport from an identifier or the default/only configured viewport."""

        if not identifier:
            try:
                return PyxelViewport.default_or_only()
            except PyxelViewport.DoesNotExist as exc:
                raise CommandError("No Pyxel viewport exists") from exc
            except PyxelViewport.MultipleObjectsReturned as exc:
                raise CommandError(str(exc)) from exc

        viewport = PyxelViewport.objects.filter(slug=identifier).first()
        if viewport is None:
            viewport = PyxelViewport.objects.filter(name=identifier).first()
        if viewport is None:
            raise CommandError(f"Viewport '{identifier}' was not found")
        return viewport

    def handle(self, *args, **options):
        if not has_graphical_display():
            raise CommandError(
                "No graphical display is configured for this shell. In WSL, ensure WSLg/X11 is available."
            )

        os.environ.update(build_graphical_subprocess_env())
        viewport = self._resolve_viewport(options.get("viewport"))
        self.stdout.write(f"Opening viewport '{viewport.name}' ({viewport.slug})")
        try:
            viewport.open_viewport()
        except PyxelUnavailableError as exc:
            raise CommandError(str(exc)) from exc
