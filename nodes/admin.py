from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import redirect
from django.utils.html import format_html
from django import forms
from django.db import models
import socket
import os

from .models import Node, NodeScreenshot, NginxConfig, Recipe, Step


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


class StepInline(admin.TabularInline):
    model = Step
    extra = 0


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    fields = ("name", "full_script")
    inlines = [StepInline]
    formfield_overrides = {
        models.TextField: {"widget": forms.Textarea(attrs={"rows": 20, "style": "width:100%"})}
    }

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        lines = [line for line in obj.full_script.splitlines() if line.strip()]
        obj.steps.all().delete()
        for idx, script in enumerate(lines, start=1):
            Step.objects.create(recipe=obj, order=idx, script=script)


@admin.register(NginxConfig)
class NginxConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "server_name", "primary_upstream", "backup_upstream")
    actions = ["test_configuration"]
    fields = (
        "name",
        "server_name",
        "primary_upstream",
        "backup_upstream",
        "listen_port",
        "ssl_certificate",
        "ssl_certificate_key",
        "rendered_config",
    )
    readonly_fields = ("rendered_config",)

    @admin.action(description="Test selected NGINX templates")
    def test_configuration(self, request, queryset):
        for cfg in queryset:
            if cfg.test_connection():
                self.message_user(request, f"{cfg.name} reachable", messages.SUCCESS)
            else:
                self.message_user(request, f"{cfg.name} unreachable", messages.ERROR)

    @admin.display(description="Generated config")
    def rendered_config(self, obj):
        return format_html(
            '<textarea readonly style="width:100%" rows="20">{}</textarea>',
            obj.config_text,
        )
