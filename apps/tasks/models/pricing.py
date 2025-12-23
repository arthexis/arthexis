from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class ManualSkill(Entity):
    """Skills that can optionally constrain manual task execution."""

    name = models.CharField(_("Name"), max_length=200)
    description = models.TextField(
        _("Description"),
        blank=True,
        help_text=_("Optional details supporting Markdown formatting."),
    )

    class Meta:
        verbose_name = _("Manual Skill")
        verbose_name_plural = _("Manual Skills")
        ordering = ("name",)
        db_table = "core_manualskill"

    def __str__(self):  # pragma: no cover - simple representation
        return self.name
