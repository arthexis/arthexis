from django.contrib import admin

from apps.core.admin import EmailCollectorAdmin, EmailInboxAdmin
from apps.locals.user_data.core import EntityModelAdmin
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
    list_display = ("name", "inbox", "outbox")
    search_fields = ("name", "inbox__username", "outbox__username")
    fieldsets = ((None, {"fields": ("name", "inbox", "outbox")}),)
