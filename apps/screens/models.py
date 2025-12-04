from collections.abc import Sequence
import importlib
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class DeviceScreen(Entity):
    """Hardware screen profile with sizing metadata."""

    MIN_REFRESH_MS = 50

    class Category(models.TextChoices):
        LCD = "lcd", _("LCD")
        LED = "led", _("LED")
        TOUCH = "touch", _("Touch")
        PIXEL = "pixel", _("Pixel matrix")
        OTHER = "other", _("Other")

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.OTHER,
        help_text=_("Broad hardware category for the screen."),
    )
    skin = models.CharField(
        max_length=100,
        help_text=_("Skin, SKU or shell identifier for the device."),
    )
    columns = models.PositiveSmallIntegerField(
        default=0,
        help_text=_(
            "Text columns for character displays or pixel width for matrix screens."
        ),
    )
    rows = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Text rows for character displays or pixel height for matrix screens."),
    )
    resolution_width = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Pixel width for graphical displays when the resolution differs from columns."),
    )
    resolution_height = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Pixel height for graphical displays when the resolution differs from rows."),
    )
    min_refresh_ms = models.PositiveIntegerField(
        default=MIN_REFRESH_MS,
        help_text=_("Minimum delay in milliseconds before accepting the next frame."),
    )
    last_bitmap = models.JSONField(
        default=list,
        blank=True,
        editable=False,
        help_text=_("Last bitmap payload received by the screen."),
    )
    last_refresh_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Device Screen")
        verbose_name_plural = _("Device Screens")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.columns and self.rows:
            dimensions = f"{self.columns}x{self.rows}"
        elif self.resolution_width and self.resolution_height:
            dimensions = f"{self.resolution_width}x{self.resolution_height}"
        else:
            dimensions = "unknown"
        return f"{self.name} ({dimensions})"

    def pixel_dimensions(self) -> tuple[int | None, int | None]:
        """Return the pixel dimensions using resolution or column/row hints."""

        width = self.resolution_width or self.columns or None
        height = self.resolution_height or self.rows or None
        return width, height

    def update_bitmap(
        self, bitmap: Sequence[Sequence[Any]], *, received_at=None, save: bool = True
    ) -> bool:
        """Persist a bitmap if it satisfies the refresh window.

        ``bitmap`` should be an iterable of rows; each row is converted to a list to
        ensure JSON serialisation. Attempts that arrive before ``min_refresh_ms`` has
        elapsed since the previous update are ignored.
        """

        now = received_at or timezone.now()
        if self.last_refresh_at:
            delta_ms = (now - self.last_refresh_at).total_seconds() * 1000
            if delta_ms < self.min_refresh_ms:
                return False

        normalized = [list(row) for row in bitmap]
        if save:
            self.last_bitmap = normalized
            self.last_refresh_at = now
            self.save(update_fields=["last_bitmap", "last_refresh_at"])
        return True


class PyxelUnavailableError(RuntimeError):
    """Raised when the Pyxel dependency cannot be imported."""


class PyxelViewport(DeviceScreen):
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
        return importlib.import_module("pyxel")

    def _render_frame(self, pyxel, width: int, height: int) -> list[list[int]]:
        return [[pyxel.pget(x, y) for x in range(width)] for y in range(height)]

    def render_bitmap(self, *, pyxel_module=None, frames: int = 1) -> list[list[int]]:
        """Execute the stored Pyxel script and return the final bitmap.

        The script must define a ``draw`` function and may define an ``update``
        function. ``frames`` dictates how many update/draw cycles run before
        capturing the bitmap. Each successful draw attempts to store the resulting
        bitmap using :py:meth:`DeviceScreen.update_bitmap` respecting the refresh
        interval.
        """

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
        final_bitmap: list[list[int]] = []
        try:
            for _ in range(max(frames, 1)):
                if callable(update_func):
                    update_func()
                draw_func()
                final_bitmap = self._render_frame(pyxel, width, height)
                self.update_bitmap(final_bitmap)
        finally:
            try:
                pyxel.quit()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        return final_bitmap

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
            self.update_bitmap(bitmap)

        pyxel.run(_update, _draw)
