from django.contrib import admin

from apps.core.admin import EmailCollectorAdmin, EmailInboxAdmin
from apps.nodes.admin import EmailOutboxAdmin

from .models import EmailCollector, EmailInbox, EmailOutbox


@admin.register(EmailInbox)
class EmailInboxAdminProxy(EmailInboxAdmin):
    pass


@admin.register(EmailCollector)
class EmailCollectorAdminProxy(EmailCollectorAdmin):
    pass


@admin.register(EmailOutbox)
class EmailOutboxAdminProxy(EmailOutboxAdmin):
    pass
