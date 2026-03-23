from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from .models import GoogleCalendar


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
