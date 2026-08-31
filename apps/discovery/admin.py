from django.contrib import admin

from .models import Discovery, DiscoveryItem


class DiscoveryItemInline(admin.TabularInline):
    model = DiscoveryItem
    extra = 0
    can_delete = False
    readonly_fields = (
        "content_type",
        "object_id",
        "label",
        "was_created",
        "was_overwritten",
        "data",
        "created_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Discovery)
class DiscoveryAdmin(admin.ModelAdmin):
    list_display = (
        "action_label",
        "app_label",
        "model_name",
        "initiated_by",
        "created_at",
        "created_count",
        "overwritten_count",
    )
    list_filter = ("app_label", "model_name", "created_at")
    search_fields = ("action_label", "app_label", "model_name")
    readonly_fields = (
        "action_label",
        "app_label",
        "model_name",
        "initiated_by",
        "metadata",
        "created_at",
    )
    inlines = [DiscoveryItemInline]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Created items")
    def created_count(self, obj):
        return obj.items.filter(was_created=True).count()

    @admin.display(description="Overwritten items")
    def overwritten_count(self, obj):
        return obj.items.filter(was_overwritten=True).count()


@admin.register(DiscoveryItem)
class DiscoveryItemAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "discovery",
        "content_type",
        "object_id",
        "was_created",
        "was_overwritten",
        "created_at",
    )
    list_filter = ("was_created", "was_overwritten", "created_at")
    search_fields = ("label", "object_id")
    readonly_fields = (
        "discovery",
        "content_type",
        "object_id",
        "label",
        "was_created",
        "was_overwritten",
        "data",
        "created_at",
    )

    def has_add_permission(self, request):
        return False
