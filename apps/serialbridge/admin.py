from django.contrib import admin

from apps.serialbridge.models import (
    SerialCommandAudit,
    SerialInterface,
    SerialPeer,
    SerialSession,
)


@admin.register(SerialInterface)
class SerialInterfaceAdmin(admin.ModelAdmin):
    list_display = ("name", "device_path", "interface_type", "role", "baud_rate", "is_enabled")
    list_filter = ("interface_type", "role", "is_enabled")
    search_fields = ("name", "device_path")


@admin.register(SerialPeer)
class SerialPeerAdmin(admin.ModelAdmin):
    list_display = ("node_id", "interface", "protocol_version", "is_active", "last_seen_at")
    list_filter = ("is_active", "protocol_version")
    search_fields = ("node_id", "shared_key_fingerprint")


@admin.register(SerialSession)
class SerialSessionAdmin(admin.ModelAdmin):
    list_display = ("interface", "peer", "status", "tx_messages", "rx_messages", "last_seen_at")
    list_filter = ("status",)


@admin.register(SerialCommandAudit)
class SerialCommandAuditAdmin(admin.ModelAdmin):
    list_display = ("command", "interface", "peer", "result", "created_at")
    list_filter = ("command", "result")
    readonly_fields = ("created_at",)
