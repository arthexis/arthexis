"""Data models for public shop catalogs and orders."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.odoo.models import OdooProduct


class Shop(Entity):
    """A storefront that exposes products and checkout."""

    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    support_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        """Return a readable representation for admin usage."""

        return self.name


class ShopProduct(Entity):
    """A purchasable product for a given shop."""

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160)
    sku = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    stock_quantity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    odoo_product = models.ForeignKey(
        OdooProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_products",
    )

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["shop", "slug"], name="shop_product_unique_slug"),
        ]

    def __str__(self) -> str:
        """Return a readable representation for admin usage."""

        return f"{self.shop.name}: {self.name}"


class ShopOrder(Entity):
    """Persisted checkout data and fulfillment metadata for a shop purchase."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        SHIPPED = "shipped", _("Shipped")
        DELIVERED = "delivered", _("Delivered")
        CANCELLED = "cancelled", _("Cancelled")

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    customer_email = models.EmailField()
    customer_name = models.CharField(max_length=140)
    payment_provider = models.CharField(max_length=64, default="manual")
    shipping_address_line1 = models.CharField(max_length=255)
    shipping_address_line2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=120)
    shipping_postal_code = models.CharField(max_length=32)
    shipping_country = models.CharField(max_length=120)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="EUR")
    odoo_sales_order_ref = models.CharField(max_length=120, blank=True)
    tracking_number = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        """Return an admin friendly label for this order."""

        return f"{self.shop.slug.upper()}-{self.pk}"

    def clean(self) -> None:
        """Enforce order monetary consistency."""

        computed_total = (self.subtotal or Decimal("0.00")) + (self.shipping_cost or Decimal("0.00"))
        if self.total != computed_total:
            raise ValidationError({"total": _("Total must equal subtotal plus shipping cost.")})

    def sync_to_odoo(self) -> str:
        """Create a deterministic simulated Odoo sales-order reference for this order."""

        if self.odoo_sales_order_ref:
            return self.odoo_sales_order_ref

        stamp = timezone.now().strftime("%Y%m%d%H%M%S")
        self.odoo_sales_order_ref = f"SO-{self.shop.slug.upper()}-{self.pk}-{stamp}"
        self.save(update_fields=["odoo_sales_order_ref", "updated_at"])
        return self.odoo_sales_order_ref


class ShopOrderItem(models.Model):
    """Line item snapshot stored for each checkout order."""

    order = models.ForeignKey(ShopOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(ShopProduct, on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=140)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        """Return a short label for this order line."""

        return f"{self.product_name} x{self.quantity}"
