from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import redirect
import socket
import os

from .models import Node


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("hostname", "address", "port", "last_seen")
    search_fields = ("hostname", "address")
    change_list_template = "admin/nodes/node/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="nodes_node_register_current",
            )
        ]
        return custom + urls

    def register_current(self, request):
        """Create a Node entry for this host if it doesn't exist."""
        hostname = socket.gethostname()
        try:
            address = socket.gethostbyname(hostname)
        except OSError:
            address = "127.0.0.1"
        port = int(os.environ.get("PORT", 8000))

        node, created = Node.objects.get_or_create(
            hostname=hostname,
            defaults={"address": address, "port": port},
        )
        if created:
            self.message_user(request, "Current host registered", messages.SUCCESS)
        else:
            self.message_user(request, "Current host already registered", messages.INFO)
        return redirect("..")
