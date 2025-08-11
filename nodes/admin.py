from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import redirect
from django.utils.html import format_html
from django import forms
from django.db import models
from django.conf import settings
from pathlib import Path
import base64
import socket
import os
import pyperclip
from pyperclip import PyperclipException
from .utils import capture_screenshot, save_screenshot

from .models import (
    Node,
    NodeScreenshot,
    NodeMessage,
    NginxConfig,
    Recipe,
    Step,
    TextSample,
    TextPattern,
)


class NodeAdminForm(forms.ModelForm):
    class Meta:
        model = Node
        fields = "__all__"
        widgets = {
            "badge_color": forms.TextInput(attrs={"type": "color"})
        }


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = (
        "hostname",
        "address",
        "port",
        "badge_color",
        "enable_public_api",
        "public_endpoint",
        "enable_clipboard_polling",
        "enable_screenshot_polling",
        "last_seen",
    )
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
    list_display = ("path", "node", "method", "created")
    change_list_template = "admin/nodes/nodescreenshot/change_list.html"
    readonly_fields = ("image_preview", "created")
    fields = ("image_preview", "path", "node", "method", "created")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "capture/",
                self.admin_site.admin_view(self.capture_now),
                name="nodes_nodescreenshot_capture",
            )
        ]
        return custom + urls

    def capture_now(self, request):
        url = request.build_absolute_uri("/")
        path = capture_screenshot(url)
        hostname = socket.gethostname()
        node = Node.objects.filter(
            hostname=hostname, port=request.get_port()
        ).first()
        screenshot = save_screenshot(path, node=node, method="ADMIN")
        if screenshot:
            self.message_user(
                request, f"Screenshot saved to {path}", messages.SUCCESS
            )
        else:
            self.message_user(
                request, "Duplicate screenshot; not saved", messages.INFO
            )
        return redirect("..")

    @admin.display(description="Screenshot")
    def image_preview(self, obj):
        if not obj or not obj.path:
            return ""
        file_path = Path(obj.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        if not file_path.exists():
            return "File not found"
        with file_path.open("rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return format_html(
            '<img src="data:image/png;base64,{}" style="max-width:100%;" />',
            encoded,
        )


@admin.register(NodeMessage)
class NodeMessageAdmin(admin.ModelAdmin):
    list_display = ("node", "method", "created")


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


@admin.register(TextSample)
class TextSampleAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at", "short_content", "automated")
    readonly_fields = ("created_at", "name")
    change_list_template = "admin/nodes/textsample/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "from-clipboard/",
                self.admin_site.admin_view(self.add_from_clipboard),
                name="nodes_textsample_from_clipboard",
            )
        ]
        return custom + urls

    def add_from_clipboard(self, request):
        try:
            content = pyperclip.paste()
        except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
            self.message_user(request, f"Clipboard error: {exc}", level=messages.ERROR)
            return redirect("..")
        if not content:
            self.message_user(request, "Clipboard is empty.", level=messages.INFO)
            return redirect("..")
        if TextSample.objects.filter(content=content).exists():
            self.message_user(
                request, "Duplicate sample not created.", level=messages.INFO
            )
            return redirect("..")
        TextSample.objects.create(content=content)
        self.message_user(
            request, "Text sample added from clipboard.", level=messages.SUCCESS
        )
        return redirect("..")

    def short_content(self, obj):
        return obj.content[:50]

    short_content.short_description = "Content"


@admin.register(TextPattern)
class TextPatternAdmin(admin.ModelAdmin):
    list_display = ("mask", "priority")
    actions = ["scan_latest_sample", "test_clipboard"]

    @admin.action(description="Scan latest sample")
    def scan_latest_sample(self, request, queryset):
        sample = TextSample.objects.first()
        if not sample:
            self.message_user(request, "No samples available.", level=messages.WARNING)
            return
        for pattern in TextPattern.objects.order_by("-priority", "id"):
            result = pattern.match(sample.content)
            if result is not None:
                if result != pattern.mask:
                    msg = f"Matched '{pattern.mask}' -> '{result}'"
                else:
                    msg = f"Matched '{pattern.mask}'"
                self.message_user(request, msg, level=messages.SUCCESS)
                return
        self.message_user(
            request,
            "No pattern matched the latest sample.",
            level=messages.INFO,
        )

    @admin.action(description="Test against clipboard")
    def test_clipboard(self, request, queryset):
        try:
            content = pyperclip.paste()
        except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
            self.message_user(request, f"Clipboard error: {exc}", level=messages.ERROR)
            return
        if not content:
            self.message_user(request, "Clipboard is empty.", level=messages.INFO)
            return
        for pattern in queryset:
            result = pattern.match(content)
            if result is not None:
                if result != pattern.mask:
                    msg = f"Matched '{pattern.mask}' -> '{result}'"
                else:
                    msg = f"Matched '{pattern.mask}'"
                self.message_user(request, msg, level=messages.SUCCESS)
            else:
                self.message_user(
                    request, f"No match for '{pattern.mask}'", level=messages.INFO
                )
