from django.contrib import admin

from apps.core.admin.mixins import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from ..models import AdminBadge


@admin.register(AdminBadge)
class AdminBadgeAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin for configurable header badges."""

    list_display = (
        "name",
        "slug",
        "label",
        "is_enabled",
        "priority",
        "owner_display",
    )
    list_filter = ("is_enabled",)
    search_fields = ("name", "slug", "label", "provider_key")
    ordering = ("priority", "name")
    exclude = ("value_query_path",)
