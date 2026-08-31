from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class OdooSaleOrderTemplate(Entity):
    """Template mapping for creating Odoo sale orders with optional defaults."""

    name = models.CharField(max_length=255)
    odoo_template = models.JSONField(
        help_text=_("Selected Odoo sale order template payload (id and name).")
    )
    note_template = models.TextField(
        blank=True,
        help_text=_("Optional default note appended to generated orders."),
    )
    resolve_note_sigils = models.BooleanField(
        default=False,
        help_text=_("Resolve SIGILS inside note template before creating the order."),
    )
    default_new_customer_language = models.CharField(
        max_length=16,
        default="es_ES",
        help_text=_("Default language used for newly created customers."),
    )
    fallback_new_customer_language = models.CharField(
        max_length=16,
        default="en_US",
        help_text=_("Fallback language for newly created customers."),
    )
    salesperson = models.ForeignKey(
        "odoo.OdooEmployee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_order_templates",
        help_text=_("Optional preset salesperson (Odoo employee) for generated orders."),
    )

    def template_id(self) -> int | None:
        value = (self.odoo_template or {}).get("id")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.name

    class Meta:
        verbose_name = _("Odoo Sale Order Template")
        verbose_name_plural = _("Odoo Sale Order Templates")
        db_table = "core_odoosaleordertemplate"


class OdooSaleFactor(Entity):
    """Parameterized factor that can inject products into generated orders."""

    name = models.CharField(max_length=255)
    code = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    templates = models.ManyToManyField(
        OdooSaleOrderTemplate,
        blank=True,
        related_name="restricted_factors",
        help_text=_(
            "Leave empty to apply to all templates. Select templates to restrict factor usage."
        ),
    )

    def applies_to_template(self, template: OdooSaleOrderTemplate) -> bool:
        if not self.pk:
            return True
        if not self.templates.exists():
            return True
        return self.templates.filter(pk=template.pk).exists()

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.name

    class Meta:
        verbose_name = _("Odoo Sale Factor")
        verbose_name_plural = _("Odoo Sale Factors")
        db_table = "core_odoosalefactor"


class OdooSaleFactorProductRule(Entity):
    """Product quantity rule controlled by one sale factor."""

    class QuantityMode(models.TextChoices):
        FIXED = "fixed", _("Fixed")
        FACTOR_LINEAR = "factor_linear", _("Linear function of factor")

    factor = models.ForeignKey(
        OdooSaleFactor,
        on_delete=models.CASCADE,
        related_name="product_rules",
    )
    name = models.CharField(max_length=255)
    odoo_product = models.JSONField(
        help_text=_("Selected Odoo product payload (id and name).")
    )
    quantity_mode = models.CharField(
        max_length=32,
        choices=QuantityMode.choices,
        default=QuantityMode.FIXED,
    )
    fixed_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("1"),
    )
    factor_multiplier = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1"),
        help_text=_("Multiplier for X when quantity mode is linear."),
    )

    def product_id(self) -> int | None:
        value = (self.odoo_product or {}).get("id")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def quantity_for_factor_value(self, value: Decimal) -> Decimal:
        if value <= 0:
            return Decimal("0")
        if self.quantity_mode == self.QuantityMode.FIXED:
            return self.fixed_quantity
        return self.factor_multiplier * value

    def clean(self):
        super().clean()
        if self.product_id() is None:
            raise ValidationError({"odoo_product": _("Choose a product with a valid Odoo ID.")})

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.name

    class Meta:
        verbose_name = _("Odoo Sale Factor Product Rule")
        verbose_name_plural = _("Odoo Sale Factor Product Rules")
        db_table = "core_odoosalefactorproductrule"
