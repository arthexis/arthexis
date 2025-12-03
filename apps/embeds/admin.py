from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from .models import EmbedLead


@admin.register(EmbedLead)
class EmbedLeadAdmin(EntityModelAdmin):
    list_display = (
        "target_url",
        "status",
        "user",
        "referer_display",
        "created_on",
    )
    list_filter = ("status",)
    search_fields = (
        "target_url",
        "referer",
        "path",
        "user__username",
        "user__email",
    )
    readonly_fields = (
        "target_url",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
    )
    fields = (
        "target_url",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "status",
        "assign_to",
        "created_on",
    )
    ordering = ("-created_on",)
    date_hierarchy = "created_on"

    @admin.display(description=_("Referrer"))
    def referer_display(self, obj):
        return obj.referer or ""
