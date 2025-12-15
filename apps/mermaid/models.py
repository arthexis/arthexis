from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class Flow(Entity):
    """Mermaid diagram definition for shared visualization."""

    name = models.CharField(
        _("Name"),
        max_length=255,
        unique=True,
        help_text=_("Human-friendly identifier for the Mermaid flow."),
    )
    description = models.TextField(
        _("Description"),
        blank=True,
        default="",
        help_text=_("Optional context explaining when to use the flow."),
    )
    definition = models.TextField(
        _("Definition"),
        help_text=_("Mermaid syntax that renders the flow diagram."),
    )

    class Meta:
        verbose_name = _("Mermaid flow")
        verbose_name_plural = _("Mermaid flows")
        db_table = "mermaid_flow"
        ordering = ("name",)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


__all__ = ["Flow"]
