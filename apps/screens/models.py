from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class DeviceScreen(Entity):
    """Hardware screen profile with sizing metadata."""

    class Category(models.TextChoices):
        LCD = "lcd", _("LCD")
        LED = "led", _("LED")
        TOUCH = "touch", _("Touch")
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
        help_text=_("Text columns supported by the display."),
    )
    rows = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Text rows supported by the display."),
    )
    resolution_width = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Pixel width for graphical displays."),
    )
    resolution_height = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Pixel height for graphical displays."),
    )

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
