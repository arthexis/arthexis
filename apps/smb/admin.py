"""Admin integration for SMB app."""

from django.contrib import admin

from apps.smb.models import SMBPartition, SMBServer


@admin.register(SMBServer)
class SMBServerAdmin(admin.ModelAdmin):
    """Admin settings for SMB servers."""

    list_display = ("name", "host", "port", "username", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "host", "username", "domain")


@admin.register(SMBPartition)
class SMBPartitionAdmin(admin.ModelAdmin):
    """Admin settings for SMB partitions."""

    list_display = (
        "name",
        "server",
        "share_name",
        "device",
        "filesystem",
        "is_enabled",
        "last_discovered_at",
    )
    list_filter = ("is_enabled", "filesystem")
    search_fields = ("name", "share_name", "device", "local_path", "server__name", "server__host")
