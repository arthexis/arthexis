from __future__ import annotations


class SiteConfigurationViewMixin:
    """Passive admin presentation for legacy nginx configuration rows."""

    list_display = (
        "name",
        "enabled",
        "mode",
        "protocol",
        "role",
        "port",
        "include_ipv6",
        "last_applied_at",
        "last_validated_at",
    )
    list_filter = ("enabled", "mode", "protocol", "include_ipv6")
    search_fields = ("name", "role")
    readonly_fields = ("last_applied_at", "last_validated_at", "last_message")
