from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class DeviceScreen(Entity):
    """Abstract hardware screen profile with sizing metadata."""

    MIN_REFRESH_MS = 50

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    skin = models.CharField(
        max_length=100,
        help_text=_("Skin, SKU or shell identifier for the device."),
    )
    columns = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Text columns for character displays or pixel width for matrix screens."),
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
    last_refresh_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True
        ordering = ["name"]
        verbose_name = _("Device Screen")
        verbose_name_plural = _("Device Screens")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        width, height = self.pixel_dimensions()
        dimensions = f"{width or 'unknown'}x{height or 'unknown'}"
        return f"{self.name} ({dimensions})"

    def pixel_dimensions(self) -> tuple[int | None, int | None]:
        """Return the pixel dimensions using resolution or column/row hints."""

        width = self.resolution_width or self.columns or None
        height = self.resolution_height or self.rows or None
        return width, height

    def _should_refresh(self, received_at=None) -> tuple[bool, Any]:
        now = received_at or timezone.now()
        if self.last_refresh_at:
            delta_ms = (now - self.last_refresh_at).total_seconds() * 1000
            if delta_ms < self.min_refresh_ms:
                return False, now
        return True, now


class CharacterScreen(DeviceScreen):
    """Character-based device screen with a text buffer."""

    character_buffer = models.TextField(
        blank=True,
        default="",
        help_text=_("Latest character payload received by the screen."),
    )
    encoding = models.CharField(
        max_length=32,
        default="utf-8",
        help_text=_("Encoding used when rendering the character buffer."),
    )

    class Meta(DeviceScreen.Meta):
        verbose_name = _("Character Screen")
        verbose_name_plural = _("Character Screens")

    def update_text(self, text: str, *, received_at=None, save: bool = True) -> bool:
        """Persist text content when within the refresh window."""

        allowed, timestamp = self._should_refresh(received_at)
        if not allowed:
            return False

        if save:
            self.character_buffer = text
            self.last_refresh_at = timestamp
            self.save(update_fields=["character_buffer", "last_refresh_at"])
        return True


class PixelScreen(DeviceScreen):
    """Pixel-based device screen with a configurable binary buffer."""

    pixel_buffer = models.BinaryField(
        blank=True,
        default=bytes,
        help_text=_("Raw pixel buffer in row-major order."),
    )
    pixel_format = models.CharField(
        max_length=16,
        default="RGB",
        help_text=_("Channel order for the pixel buffer (e.g. RGB or RGBA)."),
    )
    bytes_per_pixel = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Number of bytes used to represent a single pixel."),
    )
    row_stride = models.PositiveIntegerField(
        default=0,
        help_text=_("Optional row stride in bytes; 0 defaults to width * bytes_per_pixel."),
    )

    class Meta(DeviceScreen.Meta):
        verbose_name = _("Pixel Screen")
        verbose_name_plural = _("Pixel Screens")

    def update_pixels(
        self, buffer: bytes | bytearray | memoryview | Sequence[Sequence[Any]], *, received_at=None, save: bool = True
    ) -> bool:
        """Persist a pixel buffer when within the refresh window."""

        allowed, timestamp = self._should_refresh(received_at)
        if not allowed:
            return False

        normalized: bytes
        if isinstance(buffer, (bytes, bytearray, memoryview)):
            normalized = bytes(buffer)
        else:
            normalized = bytes(value for row in buffer for value in row)

        if save:
            self.pixel_buffer = normalized
            self.last_refresh_at = timestamp
            self.save(update_fields=["pixel_buffer", "last_refresh_at"])
        return True


class LCDAnimation(Entity):
    """Bundled or custom animation for the LCD display."""

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    source_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Path to a 32-character-per-line animation file."),
    )
    generator_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Python path to a generator that yields 32-character frames."),
    )
    frame_interval_ms = models.PositiveIntegerField(
        default=750,
        help_text=_("Preferred delay between animation frames in milliseconds."),
    )
    is_active = models.BooleanField(default=True)

    class Meta(Entity.Meta):
        ordering = ["name"]
        verbose_name = _("LCD Animation")
        verbose_name_plural = _("LCD Animations")

    def clean(self) -> None:
        if not (self.source_path or self.generator_path):
            raise ValidationError(
                {"source_path": _("Provide a source file or generator for the animation.")}
            )
        if self.source_path and self.generator_path:
            raise ValidationError(
                {"generator_path": _("Specify only one animation source.")}
            )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

