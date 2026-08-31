from .common_imports import *


class ControlOperationEventAdmin(EntityModelAdmin):
    list_display = (
        "created_at",
        "charger",
        "action",
        "transport",
        "status",
        "actor",
        "detail",
    )
    list_filter = ("transport", "status", "action")
    search_fields = ("charger__charger_id", "detail", "action", "actor__username")
    readonly_fields = (
        "charger",
        "transaction",
        "actor",
        "action",
        "transport",
        "status",
        "detail",
        "request_payload",
        "response_payload",
        "created_at",
    )
