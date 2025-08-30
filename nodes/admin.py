from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from app.widgets import CopyColorWidget, CodeEditorWidget
from django.db import models
from django.conf import settings
from pathlib import Path
from django.http import HttpResponse
import base64
import pyperclip
from pyperclip import PyperclipException
from .utils import capture_screenshot, save_screenshot
from .actions import NodeAction

from .models import (
    Node,
    NodeRole,
    ContentSample,
    NodeTask,
    NetMessage,
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
        "mac_address",
        "address",
        "port",
        "api",
        "public_endpoint",
        "clipboard",
        "screenshot",
        "installed_version",
        "role",
        "last_seen",
    )
    search_fields = ("hostname", "address", "mac_address")
    change_list_template = "admin/nodes/node/change_list.html"
    change_form_template = "admin/nodes/node/change_form.html"
    form = NodeAdminForm
    actions = ["run_task"]

    def api(self, obj):
        return obj.enable_public_api

    api.boolean = True
    api.short_description = "API"

    def clipboard(self, obj):
        return obj.clipboard_polling

    clipboard.boolean = True
    clipboard.short_description = "Clipboard"

    def screenshot(self, obj):
        return obj.screenshot_polling

    screenshot.boolean = True
    screenshot.short_description = "Screenshot"


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
            path(
                "<int:node_id>/public-key/",
                self.admin_site.admin_view(self.public_key),
                name="nodes_node_public_key",
            ),
        ]
        return custom + urls

    def register_current(self, request):
        """Create or update the Node entry for this host."""
        node, created = Node.register_current()
        if created:
            self.message_user(
                request, f"Current host registered as {node}", messages.SUCCESS
            )
        else:
            self.message_user(
                request,
                f"Current host already registered as {node}",
                messages.INFO,
            )
        return redirect("..")

    def public_key(self, request, node_id):
        node = self.get_object(request, node_id)
        if not node:
            self.message_user(request, "Unknown node", messages.ERROR)
            return redirect("..")
        security_dir = Path(settings.BASE_DIR) / "security"
        pub_path = security_dir / f"{node.public_endpoint}.pub"
        if pub_path.exists():
            response = HttpResponse(pub_path.read_bytes(), content_type="text/plain")
            response["Content-Disposition"] = f'attachment; filename="{pub_path.name}"'
            return response
        self.message_user(request, "Public key not found", messages.ERROR)
        return redirect("..")

    def run_task(self, request, queryset):
        if "apply" in request.POST:
            recipe_text = request.POST.get("recipe", "")
            task_obj, _ = NodeTask.objects.get_or_create(recipe=recipe_text)
            results = []
            for node in queryset:
                try:
                    output = task_obj.run(node)
                except Exception as exc:
                    output = str(exc)
                results.append((node, output))
            context = {"recipe": recipe_text, "results": results}
            return render(request, "admin/nodes/task_result.html", context)
        context = {"nodes": queryset}
        return render(request, "admin/nodes/node/run_task.html", context)

    run_task.short_description = "Run task"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["node_actions"] = NodeAction.get_actions()
        if object_id:
            extra_context["public_key_url"] = reverse(
                "admin:nodes_node_public_key", args=[object_id]
            )
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
            result = action_cls.run(node)
            if hasattr(result, "status_code"):
                return result
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

class NodeRoleAdminForm(forms.ModelForm):
    nodes = forms.ModelMultipleChoiceField(
        queryset=Node.objects.all(),
        required=False,
        widget=FilteredSelectMultiple("Nodes", False),
    )

    class Meta:
        model = NodeRole
        fields = ("name", "description", "nodes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["nodes"].initial = self.instance.node_set.all()


@admin.register(NodeRole)
class NodeRoleAdmin(admin.ModelAdmin):
    form = NodeRoleAdminForm
    list_display = ("name", "description")

    def save_model(self, request, obj, form, change):
        obj.node_set.set(form.cleaned_data.get("nodes", []))


@admin.register(ContentSample)
class ContentSampleAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "node", "user", "created_at")
    readonly_fields = ("created_at", "name", "user", "image_preview")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "from-clipboard/",
                self.admin_site.admin_view(self.add_from_clipboard),
                name="nodes_contentsample_from_clipboard",
            ),
            path(
                "capture/",
                self.admin_site.admin_view(self.capture_now),
                name="nodes_contentsample_capture",
            ),
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
        if ContentSample.objects.filter(content=content, kind=ContentSample.TEXT).exists():
            self.message_user(
                request, "Duplicate sample not created.", level=messages.INFO
            )
            return redirect("..")
        user = request.user if request.user.is_authenticated else None
        ContentSample.objects.create(content=content, user=user, kind=ContentSample.TEXT)
        self.message_user(
            request, "Text sample added from clipboard.", level=messages.SUCCESS
        )
        return redirect("..")

    def capture_now(self, request):
        node = Node.get_local()
        url = request.build_absolute_uri("/")
        try:
            path = capture_screenshot(url)
        except Exception as exc:  # pragma: no cover - depends on selenium setup
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        sample = save_screenshot(path, node=node, method="ADMIN")
        if sample:
            self.message_user(request, f"Screenshot saved to {path}", messages.SUCCESS)
        else:
            self.message_user(
                request, "Duplicate screenshot; not saved", messages.INFO
            )
        return redirect("..")

    @admin.display(description="Screenshot")
    def image_preview(self, obj):
        if not obj or obj.kind != ContentSample.IMAGE or not obj.path:
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


@admin.register(NetMessage)
class NetMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "body", "created", "complete")
    search_fields = ("subject", "body")
    list_filter = ("complete",)
    ordering = ("-created",)
    readonly_fields = ("complete",)
    actions = ["send_messages"]

    def send_messages(self, request, queryset):
        for msg in queryset:
            msg.propagate()
        self.message_user(request, f"{queryset.count()} messages sent")

    send_messages.short_description = "Send selected messages"


class NodeTaskForm(forms.ModelForm):
    class Meta:
        model = NodeTask
        fields = "__all__"
        widgets = {"recipe": CodeEditorWidget()}


@admin.register(NodeTask)
class NodeTaskAdmin(admin.ModelAdmin):
    form = NodeTaskForm
    list_display = ("recipe", "role", "created")
    actions = ["execute"]

    def execute(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Please select exactly one task", messages.ERROR
            )
            return
        task_obj = queryset.first()
        if "apply" in request.POST:
            node_ids = request.POST.getlist("nodes")
            nodes_qs = Node.objects.filter(pk__in=node_ids)
            results = []
            for node in nodes_qs:
                try:
                    output = task_obj.run(node)
                except Exception as exc:
                    output = str(exc)
                results.append((node, output))
            context = {"recipe": task_obj.recipe, "results": results}
            return render(request, "admin/nodes/task_result.html", context)
        nodes = Node.objects.all()
        context = {"nodes": nodes, "task_obj": task_obj}
        return render(request, "admin/nodes/nodetask/run.html", context)

    execute.short_description = "Run task on nodes"
