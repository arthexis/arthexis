from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import redirect
from django import forms
import socket
import os

from .models import Node, NodeScreenshot


class NodeAdminForm(forms.ModelForm):
    class Meta:
        model = Node
        fields = "__all__"
        widgets = {
            "badge_color": forms.TextInput(attrs={"type": "color"})
        }


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("hostname", "address", "port", "badge_color", "last_seen")
    search_fields = ("hostname", "address")
    change_list_template = "admin/nodes/node/change_list.html"
    form = NodeAdminForm

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


@admin.register(NodeScreenshot)
class NodeScreenshotAdmin(admin.ModelAdmin):
    list_display = ("path", "node", "created")
