from .common_imports import *

class ChargerLogRequestAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "log_type",
        "status",
        "last_status_at",
        "requested_at",
        "responded_at",
    )
    list_filter = ("log_type", "status")
    search_fields = (
        "charger__charger_id",
        "log_type",
        "status",
        "filename",
        "location",
    )
    date_hierarchy = "requested_at"
