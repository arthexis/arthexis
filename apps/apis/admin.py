"""Admin integration for API explorer models."""

from django.contrib import admin

from apps.apis.models import APIExplorer, ResourceMethod


class ResourceMethodInline(admin.TabularInline):
    """Inline editor for API resource methods."""

    model = ResourceMethod
    extra = 0
    fields = (
        "operation_name",
        "resource_path",
        "http_method",
    )
    show_change_link = True


@admin.register(APIExplorer)
class APIExplorerAdmin(admin.ModelAdmin):
    """Admin settings for API entry points."""

    list_display = ("name", "base_url", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "base_url", "description")
    inlines = (ResourceMethodInline,)


@admin.register(ResourceMethod)
class ResourceMethodAdmin(admin.ModelAdmin):
    """Admin settings for individual API resource methods."""

    list_display = ("operation_name", "api", "http_method", "resource_path", "updated_at")
    list_filter = ("http_method", "api")
    search_fields = ("operation_name", "resource_path", "api__name", "notes")
