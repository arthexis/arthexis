from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import redirect
from django.utils.html import format_html
from django import forms
from app.widgets import CopyColorWidget
from django.db import models
from django.conf import settings
from pathlib import Path
import base64
import socket
import os
import subprocess
import pyperclip
from pyperclip import PyperclipException
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .utils import capture_screenshot, save_screenshot
from .actions import NodeAction

from .models import (
    Node,
    NodeRole,
    NodeScreenshot,
    NodeMessage,
    NginxConfig,
    NMCLITemplate,
    SystemdUnit,
    Recipe,
    Step,
    TextSample,
    TextPattern,
)


class NodeAdminForm(forms.ModelForm):
    class Meta:
        model = Node
        fields = "__all__"
        widgets = {"badge_color": CopyColorWidget()}


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = (
        "hostname",
        "address",
        "port",
        "badge_color",
        "enable_public_api",
        "public_endpoint",
        "clipboard_polling",
        "screenshot_polling",
        "last_seen",
    )
    search_fields = ("hostname", "address")
    change_list_template = "admin/nodes/node/change_list.html"
    change_form_template = "admin/nodes/node/change_form.html"
    form = NodeAdminForm
    filter_horizontal = ("roles",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="nodes_node_register_current",
            ),
            path(
                "<int:node_id>/action/<str:action>/",
                self.admin_site.admin_view(self.action_view),
                name="nodes_node_action",
            ),
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

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["node_actions"] = NodeAction.get_actions()
        return super().changeform_view(
            request, object_id, form_url, extra_context=extra_context
        )

    def action_view(self, request, node_id, action):
        node = self.get_object(request, node_id)
        action_cls = NodeAction.registry.get(action)
        if not node or not action_cls:
            self.message_user(request, "Unknown node action", messages.ERROR)
            return redirect("..")
        try:
            action_cls.run(node)
            self.message_user(
                request,
                f"{action_cls.display_name} executed successfully",
                messages.SUCCESS,
            )
        except NotImplementedError:
            self.message_user(
                request,
                "Remote node actions are not yet implemented",
                messages.WARNING,
            )
        except Exception as exc:  # pragma: no cover - unexpected errors
            self.message_user(request, str(exc), messages.ERROR)
        return redirect(reverse("admin:nodes_node_change", args=[node_id]))


@admin.register(NodeRole)
class NodeRoleAdmin(admin.ModelAdmin):
    list_display = ("name",)


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


class NMCLITemplateResource(resources.ModelResource):
    class Meta:
        model = NMCLITemplate
        fields = (
            "connection_name",
            "assigned_device",
            "priority",
            "autoconnect",
            "static_ip",
            "static_mask",
            "static_gateway",
            "allow_outbound",
            "security_type",
            "ssid",
            "password",
            "band",
            "dns_servers",
            "ipv6_address",
            "ipv6_prefix",
            "ipv6_gateway",
            "ipv6_dns_servers",
        )
        import_id_fields = ("connection_name",)


@admin.register(NMCLITemplate)
class NMCLITemplateAdmin(ImportExportModelAdmin):
    resource_class = NMCLITemplateResource
    list_display = (
        "connection_name",
        "assigned_device",
        "priority",
        "autoconnect",
        "dns_servers",
        "ipv6_address",
    )
    actions = ["import_active", "apply_connections"]
    filter_horizontal = ("required_nodes",)
    change_list_template = "admin/nodes/nmclitemplate/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "scan/",
                self.admin_site.admin_view(self.scan_current),
                name="nodes_nmclitemplate_scan",
            )
        ]
        return custom + urls

    def _get_connection_info(self, name):
        fields = [
            "GENERAL.DEVICE",
            "GENERAL.AUTOCONNECT-PRIORITY",
            "GENERAL.AUTOCONNECT",
            "IP4.ADDRESS[1]",
            "IP4.GATEWAY",
            "IP4.NEVER_DEFAULT",
            "IP4.DNS[1]",
            "IP4.DNS[2]",
            "802-11-WIRELESS.BAND",
            "802-11-WIRELESS.SSID",
            "802-11-WIRELESS-SECURITY.KEY-MGMT",
            "802-11-WIRELESS-SECURITY.PSK",
            "IP6.ADDRESS[1]",
            "IP6.GATEWAY",
            "IP6.DNS[1]",
            "IP6.DNS[2]",
        ]
        info = {}
        for field in fields:
            try:
                details = subprocess.run(
                    ["nmcli", "-t", "-f", field, "connection", "show", name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                continue
            for line in details.stdout.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                if value:
                    info[key] = value
        return info

    @admin.action(description="Import active nmcli connections")
    def import_active(self, request, queryset):
        if os.name == "nt":
            self.message_user(request, "NMCLI unsupported on Windows", messages.WARNING)
            return
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.message_user(request, "nmcli not found", messages.ERROR)
            return
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for name in names:
            if NMCLITemplate.objects.filter(connection_name=name).exists():
                continue
            tpl = NMCLITemplate(connection_name=name)
            info = self._get_connection_info(name)
            tpl.assigned_device = info.get("GENERAL.DEVICE", "")
            tpl.priority = int(info.get("GENERAL.AUTOCONNECT-PRIORITY", "0") or 0)
            tpl.autoconnect = info.get("GENERAL.AUTOCONNECT", "").lower() == "yes"
            addr = info.get("IP4.ADDRESS[1]", "")
            if "/" in addr:
                ip, mask = addr.split("/", 1)
                tpl.static_ip = ip
                tpl.static_mask = mask
            tpl.static_gateway = info.get("IP4.GATEWAY", "") or None
            tpl.allow_outbound = info.get("IP4.NEVER_DEFAULT", "no").lower() != "yes"
            tpl.security_type = info.get("802-11-WIRELESS-SECURITY.KEY-MGMT", "")
            tpl.ssid = info.get("802-11-WIRELESS.SSID", "")
            tpl.password = info.get("802-11-WIRELESS-SECURITY.PSK", "")
            tpl.band = info.get("802-11-WIRELESS.BAND", "")
            tpl.dns_servers = ",".join(
                filter(
                    None,
                    [info.get("IP4.DNS[1]", ""), info.get("IP4.DNS[2]", "")],
                )
            )
            addr6 = info.get("IP6.ADDRESS[1]", "")
            if "/" in addr6:
                ip6, prefix6 = addr6.split("/", 1)
                tpl.ipv6_address = ip6
                try:
                    tpl.ipv6_prefix = int(prefix6)
                except ValueError:
                    tpl.ipv6_prefix = None
            tpl.ipv6_gateway = info.get("IP6.GATEWAY", "") or None
            tpl.ipv6_dns_servers = ",".join(
                filter(
                    None,
                    [info.get("IP6.DNS[1]", ""), info.get("IP6.DNS[2]", "")],
                )
            )
            tpl.save()
        self.message_user(request, "Connections imported", messages.INFO)

    def scan_current(self, request):
        if os.name == "nt":
            self.message_user(request, "NMCLI unsupported on Windows", messages.WARNING)
            return redirect("..")
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.message_user(request, "nmcli not found", messages.ERROR)
            return redirect("..")

        hostname = socket.gethostname()
        try:
            address = socket.gethostbyname(hostname)
        except OSError:
            address = "127.0.0.1"
        port = int(os.environ.get("PORT", 8000))
        node, _ = Node.objects.get_or_create(
            hostname=hostname,
            defaults={"address": address, "port": port},
        )

        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for name in names:
            if NMCLITemplate.objects.filter(connection_name=name).exists():
                continue
            tpl = NMCLITemplate(connection_name=name)
            info = self._get_connection_info(name)
            tpl.assigned_device = info.get("GENERAL.DEVICE", "")
            tpl.priority = int(info.get("GENERAL.AUTOCONNECT-PRIORITY", "0") or 0)
            tpl.autoconnect = info.get("GENERAL.AUTOCONNECT", "").lower() == "yes"
            addr = info.get("IP4.ADDRESS[1]", "")
            if "/" in addr:
                ip, mask = addr.split("/", 1)
                tpl.static_ip = ip
                tpl.static_mask = mask
            tpl.static_gateway = info.get("IP4.GATEWAY", "") or None
            tpl.allow_outbound = info.get("IP4.NEVER_DEFAULT", "no").lower() != "yes"
            tpl.security_type = info.get("802-11-WIRELESS-SECURITY.KEY-MGMT", "")
            tpl.ssid = info.get("802-11-WIRELESS.SSID", "")
            tpl.password = info.get("802-11-WIRELESS-SECURITY.PSK", "")
            tpl.band = info.get("802-11-WIRELESS.BAND", "")
            tpl.dns_servers = ",".join(
                filter(
                    None,
                    [info.get("IP4.DNS[1]", ""), info.get("IP4.DNS[2]", "")],
                )
            )
            addr6 = info.get("IP6.ADDRESS[1]", "")
            if "/" in addr6:
                ip6, prefix6 = addr6.split("/", 1)
                tpl.ipv6_address = ip6
                try:
                    tpl.ipv6_prefix = int(prefix6)
                except ValueError:
                    tpl.ipv6_prefix = None
            tpl.ipv6_gateway = info.get("IP6.GATEWAY", "") or None
            tpl.ipv6_dns_servers = ",".join(
                filter(
                    None,
                    [info.get("IP6.DNS[1]", ""), info.get("IP6.DNS[2]", "")],
                )
            )
            tpl.save()
            tpl.required_nodes.add(node)
        self.message_user(request, "Connections imported", messages.INFO)
        return redirect("..")

    @admin.action(description="Apply selected connections")
    def apply_connections(self, request, queryset):
        if os.name == "nt":
            self.message_user(request, "NMCLI unsupported on Windows", messages.WARNING)
            return
        for template in queryset:
            try:
                subprocess.run(
                    ["nmcli", "connection", "up", template.connection_name],
                    check=False,
                )
            except FileNotFoundError:
                self.message_user(request, "nmcli not found", messages.ERROR)
                return
        self.message_user(request, "Applied connections", messages.SUCCESS)


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


@admin.register(SystemdUnit)
class SystemdUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "exec_start", "installed", "running")
    fields = (
        "name",
        "description",
        "documentation",
        "user",
        "exec_start",
        "wanted_by",
        "rendered_unit",
        "installed",
        "running",
    )
    readonly_fields = ("rendered_unit", "installed", "running")

    @admin.display(description="Generated unit")
    def rendered_unit(self, obj):
        return format_html(
            '<textarea readonly style="width:100%" rows="15">{}</textarea>',
            obj.config_text,
        )

    @admin.display(description="Installed", boolean=True)
    def installed(self, obj):
        return obj.is_installed()

    @admin.display(description="Running", boolean=True)
    def running(self, obj):
        return obj.is_running()


@admin.register(TextSample)
class TextSampleAdmin(admin.ModelAdmin):
    list_display = ("name", "node", "created_at", "short_content", "automated")
    readonly_fields = ("created_at", "name", "automated")
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
        hostname = socket.gethostname()
        port = int(request.get_port())
        node = Node.objects.filter(hostname=hostname, port=port).first()
        TextSample.objects.create(content=content, node=node)
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
