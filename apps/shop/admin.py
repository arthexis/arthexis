from django.contrib import admin
from django.urls import reverse

from apps.core.admin.mixins import PublicViewLinksAdminMixin

from .models import Shop, ShopOrder, ShopOrderItem, ShopProduct


class ShopProductInline(admin.TabularInline):
    """Manage shop products directly on the shop admin page."""

    model = ShopProduct
    extra = 0


@admin.register(Shop)
class ShopAdmin(PublicViewLinksAdminMixin, admin.ModelAdmin):
    """Admin configuration for storefront records."""

    list_display = (
        "name",
        "slug",
        "is_active",
        "opening_time",
        "closing_time",
        "default_payment_provider",
        "odoo_deployment",
    )
    list_filter = ("is_active",)
    fields = (
        "name",
        "slug",
        "description",
        "is_active",
        "opening_time",
        "closing_time",
        "default_payment_provider",
        "odoo_deployment",
    )
    search_fields = ("name", "slug")
    inlines = (ShopProductInline,)

    def get_view_on_site_url(self, obj=None):
        """Return the public storefront entry point."""

        del obj
        return reverse("shop:index")

    def get_public_view_links(self, obj=None) -> list[dict[str, str]]:
        """Return public storefront routes relevant to the admin."""

        del obj
        return [{"label": "View on site: Storefront", "url": reverse("shop:index")}]


@admin.register(ShopProduct)
class ShopProductAdmin(admin.ModelAdmin):
    """Admin configuration for sellable products."""

    list_display = (
        "name",
        "shop",
        "sku",
        "unit_price",
        "currency",
        "stock_quantity",
        "supports_soul_seed_preload",
        "supports_gallery_image_printing",
        "gallery_image_print_price",
        "is_active",
    )
    list_filter = ("is_active", "supports_soul_seed_preload", "supports_gallery_image_printing", "currency", "shop")
    search_fields = ("name", "sku")


class ShopOrderItemInline(admin.TabularInline):
    """Read-only inline rendering for order line items."""

    model = ShopOrderItem
    extra = 0
    readonly_fields = (
        "product",
        "product_name",
        "sku",
        "unit_price",
        "quantity",
        "customization_surcharge_per_unit",
        "front_gallery_image",
        "back_gallery_image",
        "line_total",
    )
    can_delete = False


@admin.register(ShopOrder)
class ShopOrderAdmin(PublicViewLinksAdminMixin, admin.ModelAdmin):
    """Admin interface for placed orders and fulfillment tracking."""

    list_display = (
        "order_number",
        "shop",
        "customer_name",
        "customer_email",
        "status",
        "payment_provider",
        "total_amount",
        "created_at",
    )
    list_filter = ("status", "shop", "payment_provider")
    search_fields = ("order_number", "customer_name", "customer_email", "tracking_number")
    readonly_fields = ("order_number", "tracking_token", "created_at", "updated_at")
    inlines = (ShopOrderItemInline,)

    def get_view_on_site_url(self, obj=None):
        """Return the public order tracking route for the supplied order."""

        if obj is None:
            return None
        return reverse("shop:order_tracking", kwargs={"tracking_token": obj.tracking_token})

    def get_public_view_links(self, obj=None) -> list[dict[str, str]]:
        """Return public storefront and order-tracking routes for the admin."""

        links = [{"label": "View on site: Storefront", "url": reverse("shop:index")}]
        if obj is not None:
            links.append(
                {
                    "label": "View on site: Order tracking",
                    "url": self.get_view_on_site_url(obj),
                }
            )
        return links


@admin.register(ShopOrderItem)
class ShopOrderItemAdmin(admin.ModelAdmin):
    """Admin configuration for line items."""

    list_display = (
        "order",
        "product_name",
        "sku",
        "unit_price",
        "quantity",
        "customization_surcharge_per_unit",
        "front_gallery_image",
        "back_gallery_image",
        "line_total",
    )
    search_fields = ("product_name", "sku", "order__order_number")
