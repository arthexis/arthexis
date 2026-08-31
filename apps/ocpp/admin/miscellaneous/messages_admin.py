"""Read-only OCPP message and display record admin registrations."""

from ...models import (
    CustomerInformationChunk,
    CustomerInformationRequest,
    DataTransferMessage,
    DisplayMessage,
    DisplayMessageNotification,
)
from ..common_imports import *


@admin.register(DataTransferMessage)
class DataTransferMessageAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "connector_id",
        "direction",
        "vendor_id",
        "message_id",
        "status",
        "created_at",
        "responded_at",
    )
    list_filter = ("direction", "status")
    search_fields = (
        "charger__charger_id",
        "ocpp_message_id",
        "vendor_id",
        "message_id",
    )
    readonly_fields = (
        "charger",
        "connector_id",
        "direction",
        "ocpp_message_id",
        "vendor_id",
        "message_id",
        "payload",
        "status",
        "response_data",
        "error_code",
        "error_description",
        "error_details",
        "responded_at",
        "created_at",
        "updated_at",
    )


@admin.register(CustomerInformationRequest)
class CustomerInformationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "ocpp_message_id",
        "last_notified_at",
        "completed_at",
        "created_at",
    )
    search_fields = ("charger__charger_id", "request_id", "ocpp_message_id")
    readonly_fields = (
        "charger",
        "ocpp_message_id",
        "request_id",
        "payload",
        "last_notified_at",
        "completed_at",
        "created_at",
        "updated_at",
    )


@admin.register(CustomerInformationChunk)
class CustomerInformationChunkAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "ocpp_message_id",
        "tbc",
        "received_at",
    )
    list_filter = ("tbc",)
    search_fields = ("charger__charger_id", "request_id", "ocpp_message_id")
    readonly_fields = (
        "charger",
        "request_record",
        "ocpp_message_id",
        "request_id",
        "data",
        "tbc",
        "raw_payload",
        "received_at",
    )


@admin.register(DisplayMessageNotification)
class DisplayMessageNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "ocpp_message_id",
        "tbc",
        "received_at",
        "completed_at",
    )
    list_filter = ("tbc",)
    search_fields = ("charger__charger_id", "request_id", "ocpp_message_id")
    readonly_fields = (
        "charger",
        "ocpp_message_id",
        "request_id",
        "tbc",
        "raw_payload",
        "received_at",
        "completed_at",
        "updated_at",
    )


@admin.register(DisplayMessage)
class DisplayMessageAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "message_id",
        "priority",
        "state",
        "valid_from",
        "valid_to",
        "language",
        "created_at",
    )
    list_filter = ("priority", "state", "language")
    search_fields = ("charger__charger_id", "message_id", "content")
    readonly_fields = (
        "notification",
        "charger",
        "message_id",
        "priority",
        "state",
        "valid_from",
        "valid_to",
        "language",
        "content",
        "component_name",
        "component_instance",
        "variable_name",
        "variable_instance",
        "raw_payload",
        "created_at",
    )
