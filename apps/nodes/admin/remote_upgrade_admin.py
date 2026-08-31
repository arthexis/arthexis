from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import RemoteUpgradeRequest


@admin.register(RemoteUpgradeRequest)
class RemoteUpgradeRequestAdmin(EntityModelAdmin):
    """Inspect remote upgrade request decisions and responses."""

    list_display = (
        "uuid",
        "origin_node",
        "target_node",
        "channel",
        "status",
        "trigger_result",
        "created_date_display",
        "responded_date_display",
    )
    list_filter = ("status", "channel")
    search_fields = (
        "uuid",
        "reason",
        "rejection_reason",
        "origin_node__hostname",
        "target_node__hostname",
    )
    readonly_fields = (
        "uuid",
        "origin_uuid",
        "target_uuid",
        "created",
        "updated",
        "accepted_at",
        "rejected_at",
        "queued_at",
        "responded_at",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "uuid",
                    "origin_node",
                    "target_node",
                    "origin_uuid",
                    "target_uuid",
                    "channel",
                    "options",
                    "reason",
                )
            },
        ),
        (
            _("Decision"),
            {
                "fields": (
                    "status",
                    "rejection_reason",
                    "trigger_result",
                )
            },
        ),
        (
            _("Timeline"),
            {
                "fields": (
                    "expires_at",
                    "accepted_at",
                    "rejected_at",
                    "queued_at",
                    "responded_at",
                    "created",
                    "updated",
                )
            },
        ),
    )

    @admin.display(description=_("Created"), ordering="created")
    def created_date_display(self, obj):
        return timezone.localtime(obj.created).isoformat(timespec="minutes")

    @admin.display(description=_("Responded"), ordering="responded_at")
    def responded_date_display(self, obj):
        if not obj.responded_at:
            return ""
        return timezone.localtime(obj.responded_at).isoformat(timespec="minutes")
