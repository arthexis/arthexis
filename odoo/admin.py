from django.contrib import admin, messages

from .models import Instance

import xmlrpc.client
from urllib.parse import urljoin


@admin.register(Instance)
class InstanceAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "database", "username")
    actions = ["test_connection"]

    def test_connection(self, request, queryset):
        for instance in queryset:
            server = xmlrpc.client.ServerProxy(
                urljoin(instance.url, "/xmlrpc/2/common")
            )
            try:
                uid = server.authenticate(
                    instance.database,
                    instance.username,
                    instance.password,
                    {},
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f"{instance.name}: {exc}",
                    level=messages.ERROR,
                )
                continue

            if uid:
                self.message_user(request, f"{instance.name}: success")
            else:
                self.message_user(
                    request,
                    f"{instance.name}: invalid credentials",
                    level=messages.ERROR,
                )

    test_connection.short_description = "Test connection"
