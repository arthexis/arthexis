from django.contrib import admin

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from .models import GoogleAccount, GoogleCalendar


@admin.register(GoogleAccount)
class GoogleAccountAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin UI for Google OAuth account records used by calendar publishing."""

    list_display = ("email", "user", "group", "is_enabled")
    search_fields = ("email", "user__username", "group__name")
    list_filter = ("is_enabled",)


@admin.register(GoogleCalendar)
class GoogleCalendarAdmin(EntityModelAdmin):
    """Admin configuration for outbound Google Calendar destinations.

    Attributes:
        list_display: Core destination fields shown in the change list.
        list_filter: Filters for enabled state and timezone.
        search_fields: Lookup fields for destination and account identity.
        autocomplete_fields: Related account selector for large account tables.
    """

    list_display = ("name", "calendar_id", "account", "timezone", "is_enabled")
    list_filter = ("is_enabled", "timezone")
    search_fields = ("name", "calendar_id", "account__email")
    autocomplete_fields = ("account",)
