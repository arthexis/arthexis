"""Admin configuration for shop models."""

from django.contrib import admin

from apps.shop.models import Shop, ShopOrder, ShopOrderItem, ShopProduct


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    """Manage storefront metadata."""

    list_display = ("name", "slug", "is_active", "support_email")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "support_email")


@admin.register(ShopProduct)
class ShopProductAdmin(admin.ModelAdmin):
    """Manage purchasable shop products."""

    list_display = ("name", "shop", "unit_price", "currency", "stock_quantity", "is_active")
    list_filter = ("shop", "is_active", "currency")
    search_fields = ("name", "slug", "sku")


class ShopOrderItemInline(admin.TabularInline):
    """Display order lines on parent orders."""

    model = ShopOrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "quantity", "unit_price", "line_total")


@admin.register(ShopOrder)
class ShopOrderAdmin(admin.ModelAdmin):
    """Manage checkout orders and shipping status."""

    list_display = (
        "id",
        "shop",
        "customer_name",
        "customer_email",
        "status",
        "total",
        "currency",
        "payment_provider",
        "tracking_number",
        "created_at",
    )
    list_filter = ("shop", "status", "currency", "payment_provider")
    search_fields = (
        "customer_name",
        "customer_email",
        "tracking_number",
        "odoo_sales_order_ref",
    )
    readonly_fields = ("created_at", "updated_at", "subtotal", "shipping_cost", "total")
    inlines = [ShopOrderItemInline]


@admin.register(ShopOrderItem)
class ShopOrderItemAdmin(admin.ModelAdmin):
    """Read-only order lines for auditing."""

    list_display = ("order", "product_name", "quantity", "unit_price", "line_total")
    search_fields = ("product_name",)
