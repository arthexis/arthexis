from django.contrib import admin

from .models import Shop, ShopOrder, ShopOrderItem, ShopProduct


class ShopProductInline(admin.TabularInline):
    """Manage shop products directly on the shop admin page."""

    model = ShopProduct
    extra = 0


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
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


@admin.register(ShopProduct)
class ShopProductAdmin(admin.ModelAdmin):
    """Admin configuration for sellable products."""

    list_display = ("name", "shop", "sku", "unit_price", "currency", "stock_quantity", "is_active")
    list_filter = ("is_active", "currency", "shop")
    search_fields = ("name", "sku")


class ShopOrderItemInline(admin.TabularInline):
    """Read-only inline rendering for order line items."""

    model = ShopOrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "sku", "unit_price", "quantity", "line_total")
    can_delete = False


@admin.register(ShopOrder)
class ShopOrderAdmin(admin.ModelAdmin):
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


@admin.register(ShopOrderItem)
class ShopOrderItemAdmin(admin.ModelAdmin):
    """Admin configuration for line items."""

    list_display = ("order", "product_name", "sku", "unit_price", "quantity", "line_total")
    search_fields = ("product_name", "sku", "order__order_number")
