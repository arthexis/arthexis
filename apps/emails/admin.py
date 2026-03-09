from django.contrib import admin
from django.db.models import Max
from django.utils.translation import gettext_lazy as _

from apps.core.admin import EmailCollectorAdmin, EmailInboxAdmin
from apps.core.admin.metrics import annotate_enabled_total, format_enabled_total, max_attr
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.admin import EmailOutboxAdmin

from .models import EmailBridge, EmailCollector, EmailInbox, EmailOutbox


@admin.register(EmailInbox)
class EmailInboxAdminProxy(EmailInboxAdmin):
    pass


@admin.register(EmailCollector)
class EmailCollectorAdminProxy(EmailCollectorAdmin):
    pass


@admin.register(EmailOutbox)
class EmailOutboxAdminProxy(EmailOutboxAdmin):
    pass


@admin.register(EmailBridge)
class EmailBridgeAdmin(EntityModelAdmin):
    list_display = ("name", "inbox", "outbox", "collector_count", "last_used_at")
    search_fields = ("name", "inbox__username", "outbox__username")
    fieldsets = ((None, {"fields": ("name", "inbox", "outbox")}),)

    def get_queryset(self, request):
        queryset = annotate_enabled_total(
            super().get_queryset(request),
            "inbox__collectors",
            total_alias="total_collectors",
            enabled_alias="enabled_collectors",
        )
        return queryset.annotate(
            last_inbox_used_at=Max("inbox__transactions__processed_at"),
            last_outbox_used_at=Max("outbox__transactions__processed_at"),
        )

    @admin.display(description=_("Collectors"), ordering="enabled_collectors")
    def collector_count(self, obj):
        return format_enabled_total(
            obj,
            enabled_attr="enabled_collectors",
            total_attr="total_collectors",
        )

    @admin.display(description=_("Last used"))
    def last_used_at(self, obj):
        return max_attr(obj, "last_inbox_used_at", "last_outbox_used_at") or "-"
