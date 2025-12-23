from apps.core.models import InviteLead
from apps.locals.user_data import EntityModelAdmin


class InviteLeadAdmin(EntityModelAdmin):
    list_display = (
        "email",
        "status",
        "assign_to",
        "mac_address",
        "created_on",
        "sent_on",
        "sent_via_outbox",
        "short_error",
    )
    list_filter = ("status",)
    search_fields = ("email", "comment")
    raw_id_fields = ("assign_to",)
    readonly_fields = (
        "created_on",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "mac_address",
        "sent_on",
        "sent_via_outbox",
        "error",
    )

    def short_error(self, obj):
        return (obj.error[:40] + "…") if len(obj.error) > 40 else obj.error

    short_error.short_description = "error"
