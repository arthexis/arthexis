import importlib
from collections.abc import Sequence

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.screens.models import PixelScreen


class PyxelUnavailableError(RuntimeError):
    """Raised when the Pyxel dependency cannot be imported."""


class PyxelViewport(PixelScreen):
    """Device screen driven by a Pyxel script."""

    pyxel_script = models.TextField(
        help_text=_(
            "Python code executed with a `pyxel` variable available. Define a "
            "`draw()` function (and optional `update()`) to populate the viewport."
        )
    )
    pyxel_fps = models.PositiveSmallIntegerField(
        default=20,
        help_text=_("Frame rate passed to Pyxel when rendering this viewport."),
    )

    class Meta:
        verbose_name = _("Pyxel viewport")
        verbose_name_plural = _("Pyxel viewports")

    def _import_pyxel(self, pyxel_module=None):
        if pyxel_module is not None:
            return pyxel_module
        if importlib.util.find_spec("pyxel") is None:  # pragma: no cover - import guard
            raise PyxelUnavailableError("Pyxel library is required for this viewport")
        try:
            return importlib.import_module("pyxel")
        except Exception as exc:  # pragma: no cover - dependency import guard
            raise PyxelUnavailableError("Pyxel library is required for this viewport") from exc

    def _render_frame(self, pyxel, width: int, height: int) -> list[list[int]]:
        return [[pyxel.pget(x, y) for x in range(width)] for y in range(height)]

    def render_bitmap(self, *, pyxel_module=None, frames: int = 1) -> bytes:
        """Execute the stored Pyxel script and return the final pixel buffer."""

        pyxel = self._import_pyxel(pyxel_module)
        width, height = self.pixel_dimensions()
        if not width or not height:
            raise ValueError("Pyxel viewports require defined pixel dimensions")

        namespace = {"pyxel": pyxel}
        exec(self.pyxel_script, namespace)
        update_func = namespace.get("update")
        draw_func = namespace.get("draw")
        if draw_func is None:
            raise ValueError("Pyxel scripts must define a draw() function")

        pyxel.init(width, height, title=self.name, fps=self.pyxel_fps)
        final_bitmap: Sequence[Sequence[int]] = []
        try:
            for _ in range(max(frames, 1)):
                if callable(update_func):
                    update_func()
                draw_func()
                final_bitmap = self._render_frame(pyxel, width, height)
                self.update_pixels(final_bitmap)
        finally:
            try:
                pyxel.quit()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        return bytes(value for row in final_bitmap for value in row)

    def open_viewport(self, *, pyxel_module=None) -> None:
        """Open the Pyxel viewport window and stream frames to the device."""

        pyxel = self._import_pyxel(pyxel_module)
        width, height = self.pixel_dimensions()
        if not width or not height:
            raise ValueError("Pyxel viewports require defined pixel dimensions")

        namespace = {"pyxel": pyxel}
        exec(self.pyxel_script, namespace)
        update_func = namespace.get("update")
        draw_func = namespace.get("draw")
        if draw_func is None:
            raise ValueError("Pyxel scripts must define a draw() function")

        pyxel.init(width, height, title=self.name, fps=self.pyxel_fps)

        def _update():
            if callable(update_func):
                update_func()

        def _draw():
            draw_func()
            bitmap = self._render_frame(pyxel, width, height)
            self.update_pixels(bitmap)

        pyxel.run(_update, _draw)
