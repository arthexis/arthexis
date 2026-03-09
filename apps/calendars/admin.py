from django.contrib import admin

from apps.locals.admin_mixins import EntityModelAdmin

from .models import (
    CalendarEventDispatch,
    CalendarEventSnapshot,
    CalendarEventTrigger,
    GoogleCalendar,
)


@admin.register(GoogleCalendar)
class GoogleCalendarAdmin(EntityModelAdmin):
    list_display = ("name", "calendar_id", "account", "timezone", "is_enabled")
    list_filter = ("is_enabled", "timezone")
    search_fields = ("name", "calendar_id", "account__email")
    autocomplete_fields = ("account",)


@admin.register(CalendarEventTrigger)
class CalendarEventTriggerAdmin(EntityModelAdmin):
    list_display = ("name", "calendar", "task_name", "lead_time_minutes", "is_enabled")
    list_filter = ("is_enabled", "calendar")
    search_fields = ("name", "task_name", "calendar__name", "summary_contains", "location_contains")
    autocomplete_fields = ("calendar",)


@admin.register(CalendarEventSnapshot)
class CalendarEventSnapshotAdmin(EntityModelAdmin):
    list_display = ("calendar", "summary", "starts_at", "ends_at", "event_updated")
    list_filter = ("calendar",)
    search_fields = ("calendar__name", "summary", "location", "event_id")
    autocomplete_fields = ("calendar",)


@admin.register(CalendarEventDispatch)
class CalendarEventDispatchAdmin(EntityModelAdmin):
    list_display = ("trigger", "event_id", "event_updated")
    list_filter = ("trigger",)
    search_fields = ("event_id", "trigger__name", "trigger__calendar__name")
    autocomplete_fields = ("trigger",)
