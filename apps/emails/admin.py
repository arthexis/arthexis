from django.contrib import admin
from django.db.models import Count, Max, Q
from django.utils.translation import gettext_lazy as _

from apps.core.admin import EmailCollectorAdmin, EmailInboxAdmin
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
        return (
            super()
            .get_queryset(request)
            .annotate(
                total_collectors=Count("inbox__collectors", distinct=True),
                enabled_collectors=Count(
                    "inbox__collectors",
                    filter=Q(inbox__collectors__is_enabled=True),
                    distinct=True,
                ),
                last_inbox_used_at=Max("inbox__transactions__processed_at"),
                last_outbox_used_at=Max("outbox__transactions__processed_at"),
            )
        )

    @admin.display(description=_("Collectors"), ordering="enabled_collectors")
    def collector_count(self, obj):
        enabled = getattr(obj, "enabled_collectors", 0)
        total = getattr(obj, "total_collectors", 0)
        return f"{enabled}/{total}"

    @admin.display(description=_("Last used"))
    def last_used_at(self, obj):
        values = [
            value
            for value in (
                getattr(obj, "last_inbox_used_at", None),
                getattr(obj, "last_outbox_used_at", None),
            )
            if value is not None
        ]
        if not values:
            return "-"
        return max(values)
