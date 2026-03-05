"""Admin registrations for the users app."""

from django.contrib import admin

from apps.core.admin.mixins import OwnableAdminMixin

from .models import ChatProfile


@admin.register(ChatProfile)
class ChatProfileAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Manage per-owner chat preferences."""

    list_display = (
        "id",
        "owner_display",
        "contact_via_chat",
        "is_enabled",
    )
    list_filter = ("contact_via_chat", "is_enabled")
    search_fields = ("user__username", "group__name", "avatar__name")


__all__ = ["admin"]
