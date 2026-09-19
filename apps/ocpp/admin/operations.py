from django.contrib import admin

from apps.ocpp.models import (
    CertificateRecord,
    ChargingProfile,
    MonitoringRecord,
    NotificationRecord,
    OperationalStatusRecord,
    ProtocolOperation,
)


@admin.register(ProtocolOperation)
class ProtocolOperationAdmin(admin.ModelAdmin):
    list_display = (
        "action",
        "charger",
        "version",
        "direction",
        "status",
        "created_at",
        "completed_at",
        "terminal_outcome",
    )
    list_filter = ("version", "direction", "status")
    search_fields = ("action", "charger__identity")
    readonly_fields = (
        "charger",
        "version",
        "direction",
        "action",
        "unique_id",
        "status",
        "created_at",
        "completed_at",
        "terminal_outcome",
    )
    fields = readonly_fields

    @admin.display(description="Outcome")
    def terminal_outcome(self, operation: ProtocolOperation) -> str:
        """Show a terminal summary without exposing protocol payloads."""
        return operation.error_code or operation.get_status_display()


@admin.register(ChargingProfile)
class ChargingProfileAdmin(admin.ModelAdmin):
    list_display = ("remote_id", "charger", "purpose", "kind", "active")
    list_filter = ("purpose", "kind", "active")
    search_fields = ("remote_id",)


@admin.register(NotificationRecord)
class NotificationRecordAdmin(admin.ModelAdmin):
    list_display = ("action", "charger", "received_at")
    list_filter = ("action",)
    search_fields = ("action", "charger__identity")


@admin.register(MonitoringRecord)
class MonitoringRecordAdmin(admin.ModelAdmin):
    list_display = ("event_type", "charger", "severity", "occurred_at")
    list_filter = ("event_type",)


@admin.register(CertificateRecord)
class CertificateRecordAdmin(admin.ModelAdmin):
    list_display = (
        "fingerprint",
        "charger",
        "certificate_type",
        "status",
        "expires_at",
    )
    list_filter = ("certificate_type", "status")
    search_fields = ("fingerprint",)


@admin.register(OperationalStatusRecord)
class OperationalStatusRecordAdmin(admin.ModelAdmin):
    list_display = ("kind", "status", "charger", "occurred_at")
    list_filter = ("kind", "status")
