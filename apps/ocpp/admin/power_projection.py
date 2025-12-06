from .common_imports import *

class PowerProjectionAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "connector_id",
        "status",
        "schedule_start",
        "duration_seconds",
        "received_at",
    )
    list_filter = ("status",)
    search_fields = ("charger__charger_id", "charger__display_name")
    ordering = ("-received_at", "-requested_at")
    autocomplete_fields = ("charger",)
    readonly_fields = ("raw_response", "requested_at", "received_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("charger", "connector_id", "status")}),
        (
            _("Schedule"),
            {
                "fields": (
                    "schedule_start",
                    "duration_seconds",
                    "charging_rate_unit",
                    "charging_schedule_periods",
                )
            },
        ),
        (
            _("Response"),
            {
                "fields": (
                    "raw_response",
                    "requested_at",
                    "received_at",
                    "updated_at",
                )
            },
        ),
    )
