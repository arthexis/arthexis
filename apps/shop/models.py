from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.core.entity import Entity


class Shop(Entity):
    """A storefront that groups products and checkout behavior."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    default_payment_provider = models.CharField(max_length=80, blank=True)
    opening_time = models.TimeField(null=True, blank=True)
    closing_time = models.TimeField(null=True, blank=True)
    odoo_deployment = models.ForeignKey(
        "odoo.OdooDeployment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shops",
    )

    class Meta:
        ordering = ("name",)
        constraints = [
            models.CheckConstraint(
                condition=(Q(opening_time__isnull=True, closing_time__isnull=True) | Q(opening_time__isnull=False, closing_time__isnull=False)),
                name="shop_business_hours_both_set_or_null",
            )
        ]

    def clean(self):
        """Require opening and closing time fields to be configured as a pair."""

        super().clean()
        if (self.opening_time is None) != (self.closing_time is None):
            raise ValidationError({"opening_time": "Opening and closing times must both be set or both left blank."})

    def save(self, *args, **kwargs):
        """Populate a slug from name when not explicitly provided."""

        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def has_business_hours(self) -> bool:
        """Return whether both opening and closing times are configured."""

        return bool(self.opening_time and self.closing_time)

    def is_open_at(self, current_time: time) -> bool:
        """Return whether the shop is open for the supplied local time."""

        if not self.has_business_hours():
            return True

        if self.opening_time == self.closing_time:
            return True

        if self.opening_time < self.closing_time:
            return self.opening_time <= current_time < self.closing_time

        return current_time >= self.opening_time or current_time < self.closing_time

    def next_opening_datetime(self, reference: datetime) -> datetime | None:
        """Return the next local opening datetime when business hours are configured."""

        if not self.has_business_hours():
            return None

        if self.opening_time == self.closing_time:
            return None

        opening_today = datetime.combine(reference.date(), self.opening_time, tzinfo=reference.tzinfo)

        if self.opening_time < self.closing_time:
            if reference < opening_today:
                return opening_today
            return opening_today + timedelta(days=1)

        if reference.time() >= self.opening_time:
            return opening_today + timedelta(days=1)
        return opening_today

    def __str__(self) -> str:
        """Return a readable representation."""

        return self.name


class ShopProduct(Entity):
    """Product offered through a specific shop and linked to Odoo product data."""

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=120)
    sku = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=8, default="EUR")
    stock_quantity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    supports_soul_seed_preload = models.BooleanField(default=False)
    supports_gallery_image_printing = models.BooleanField(default=False)
    gallery_image_print_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Additional price per selected side (front or back) when printing with gallery images.",
    )
    odoo_product = models.ForeignKey(
        "odoo.OdooProduct",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_products",
    )

    class Meta:
        ordering = ("name",)
        unique_together = ("shop", "sku")

    def __str__(self) -> str:
        """Return product name."""

        return self.name


class ShopOrder(Entity):
    """A captured checkout including shipping and payment metadata."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"

    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="orders")
    order_number = models.CharField(max_length=20, unique=True)
    tracking_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    customer_name = models.CharField(max_length=120)
    customer_email = models.EmailField()
    shipping_address_line1 = models.CharField(max_length=200)
    shipping_address_line2 = models.CharField(max_length=200, blank=True)
    shipping_city = models.CharField(max_length=80)
    shipping_postal_code = models.CharField(max_length=20)
    shipping_country = models.CharField(max_length=80)
    payment_provider = models.CharField(max_length=80, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    odoo_sales_order_reference = models.CharField(max_length=120, blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)
    tracking_url = models.URLField(blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    shipped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        """Assign a simple chronological order number when needed."""

        if not self.order_number:
            self.order_number = timezone.now().strftime("SO%y%m%d%H%M%S%f")[:20]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return order label used in admin and tracking views."""

        return self.order_number


class ShopOrderItem(Entity):
    """A snapshot of product pricing and quantity on an order."""

    order = models.ForeignKey(ShopOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        ShopProduct,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_items",
    )
    odoo_product = models.ForeignKey(
        "odoo.OdooProduct",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_items",
    )
    product_name = models.CharField(max_length=120)
    sku = models.CharField(max_length=80, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    line_total = models.DecimalField(max_digits=10, decimal_places=2)
    front_gallery_image = models.ForeignKey(
        "gallery.GalleryImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_order_items_front",
    )
    back_gallery_image = models.ForeignKey(
        "gallery.GalleryImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_order_items_back",
    )
    customization_surcharge_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    def __str__(self) -> str:
        """Return display name with quantity for admin readability."""

        return f"{self.product_name} x{self.quantity}"
