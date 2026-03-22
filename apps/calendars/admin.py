from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from .models import GoogleCalendar


@admin.register(GoogleCalendar)
class GoogleCalendarAdmin(EntityModelAdmin):
    list_display = ("name", "calendar_id", "account", "timezone", "is_enabled")
    list_filter = ("is_enabled", "timezone")
    search_fields = ("name", "calendar_id", "account__email")
    autocomplete_fields = ("account",)
