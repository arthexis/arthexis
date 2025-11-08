from __future__ import annotations

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from core.admin import EntityModelAdmin

from .models import CPForwarder


@admin.register(CPForwarder)
class CPForwarderAdmin(EntityModelAdmin):
    list_display = (
        "display_name",
        "target_node",
        "enabled",
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
    )
    list_filter = ("enabled", "is_running", "target_node")
    search_fields = (
        "name",
        "target_node__hostname",
        "target_node__public_endpoint",
        "target_node__address",
    )
    autocomplete_fields = ["target_node", "source_node"]
    readonly_fields = (
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    actions = ["test_forwarders"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "source_node",
                    "target_node",
                    "enabled",
                    "is_running",
                    "last_forwarded_at",
                    "last_status",
                    "last_error",
                    "last_synced_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description=_("Name"))
    def display_name(self, obj):
        if obj.name:
            return obj.name
        if obj.target_node:
            return str(obj.target_node)
        return _("Forwarder")

    @admin.action(description=_("Test forwarder configuration"))
    def test_forwarders(self, request, queryset):
        tested = 0
        for forwarder in queryset:
            forwarder.sync_chargers()
            tested += 1
        if tested:
            self.message_user(
                request,
                _("Tested %(count)s forwarder(s).") % {"count": tested},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
