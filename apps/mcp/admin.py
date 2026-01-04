from django.contrib import admin

from .models import MCPServer


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "acting_user",
        "is_enabled",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_enabled",)
    search_fields = ("name", "slug", "acting_user__username")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "acting_user", "is_enabled", "api_secret")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
