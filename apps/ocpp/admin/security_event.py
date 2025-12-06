from .common_imports import *

class SecurityEventAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "event_type",
        "event_timestamp",
        "trigger",
        "sequence_number",
    )
    list_filter = ("event_type",)
    search_fields = ("charger__charger_id", "event_type", "tech_info")
    date_hierarchy = "event_timestamp"
