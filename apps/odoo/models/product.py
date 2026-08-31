from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class OdooProduct(Entity):
    """A product defined in Odoo that users can subscribe to."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    renewal_period = models.PositiveIntegerField(help_text="Renewal period in days")
    odoo_product = models.JSONField(
        null=True,
        blank=True,
        help_text="Selected product from Odoo (id and name)",
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Odoo Product")
        verbose_name_plural = _("Odoo Products")
        db_table = "core_odoo_product"
