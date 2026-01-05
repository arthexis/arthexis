from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import NetMessage
from .forms import NetMessageAdminForm, QuickSendForm

@admin.register(NetMessage)
class NetMessageAdmin(EntityModelAdmin):
    change_list_template = "admin/nodes/netmessage/change_list.html"
    form = NetMessageAdminForm
    change_form_template = "admin/nodes/netmessage/change_form.html"
    list_display = (
        "subject",
        "body",
        "expires_at",
        "filter_node_role_display",
        "node_origin_display",
        "created_date_display",
        "target_limit_display",
        "complete",
    )
    search_fields = ("subject", "body")
    list_filter = ("complete", "filter_node_role", "filter_current_relation")
    ordering = ("-created",)
    readonly_fields = ("complete",)
    actions = ["send_messages"]
    fieldsets = (
        (None, {"fields": ("subject", "body", "expires_at")}),
        (
            "Filters",
            {
                "fields": (
                    "filter_node",
                    "filter_node_feature",
                    "filter_node_role",
                    "filter_current_relation",
                    "filter_installed_version",
                    "filter_installed_revision",
                )
            },
        ),
        (_("LCD Display"), {"fields": ("lcd_channel_type", "lcd_channel_num")}),
        ("Attachments", {"fields": ("attachments",)}),
        (
            "Propagation",
            {
                "fields": (
                    "node_origin",
                    "target_limit",
                    "propagated_to",
                    "complete",
                )
            },
        ),
    )
    quick_send_fieldsets = (
        (None, {"fields": ("subject", "body")}),
        (
            _("Filters"),
            {
                "fields": (
                    "filter_node",
                    "filter_node_feature",
                    "filter_node_role",
                    "filter_current_relation",
                    "filter_installed_version",
                    "filter_installed_revision",
                )
            },
        ),
        (_("LCD Display"), {"fields": ("lcd_channel_type", "lcd_channel_num")}),
        (
            _("Propagation"),
            {
                "fields": (
                    "expires_at",
                    "target_limit",
                )
            },
        ),
    )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.has_add_permission(request):
            action = getattr(self, "send", None)
            if action is not None and "send" not in actions:
                actions["send"] = (
                    action,
                    "send",
                    getattr(action, "short_description", _("Send Net Message")),
                )
        return actions

    def send(self, request, queryset=None):
        return redirect(
            reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_send"
            )
        )

    send.label = _("Send Net Message")
    send.short_description = _("Send Net Message")

    def get_urls(self):
        urls = super().get_urls()
        opts = self.model._meta
        custom_urls = [
            path(
                "<path:object_id>/resend/",
                self.admin_site.admin_view(self.resend_message),
                name=f"{opts.app_label}_{opts.model_name}_resend",
            ),
            path(
                "send/",
                self.admin_site.admin_view(self.send_tool_view),
                name=f"{opts.app_label}_{opts.model_name}_send",
            )
        ]
        return custom_urls + urls

    def resend_message(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        if not self.has_change_permission(request):
            raise PermissionDenied

        net_message = self.get_object(request, object_id)
        if not net_message:
            self.message_user(request, _("Net Message not found."), messages.ERROR)
            changelist_url = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"
            )
            return redirect(changelist_url)

        if not self.has_change_permission(request, net_message):
            raise PermissionDenied

        try:
            net_message.propagate()
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.log_change(request, net_message, _("Resent net message"))
            self.message_user(
                request,
                _("Net Message resent to the network."),
                level=messages.SUCCESS,
            )

        change_url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
            args=[object_id],
        )
        return redirect(change_url)

    def send_tool_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        form_class = QuickSendForm
        if request.method == "POST":
            form = form_class(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                obj.pk = None
                previous_skip_flag = getattr(self, "_skip_entity_user_datum", False)
                self._skip_entity_user_datum = True
                try:
                    self.save_model(request, obj, form, change=False)
                    self.save_related(request, form, formsets=[], change=False)
                finally:
                    self._skip_entity_user_datum = previous_skip_flag
                self.log_addition(
                    request,
                    obj,
                    self.construct_change_message(request, form, None),
                )
                obj.propagate()
                self.message_user(
                    request,
                    _("Net Message sent to the network."),
                    level=messages.SUCCESS,
                )
                changelist_url = reverse(
                    f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"
                )
                return redirect(changelist_url)
        else:
            form = form_class()

        admin_form = helpers.AdminForm(form, self.quick_send_fieldsets, {})
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Send Net Message"),
            "adminform": admin_form,
            "media": self.media + form.media,
        }
        return TemplateResponse(
            request,
            "admin/nodes/netmessage/send.html",
            context,
        )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["resend_url"] = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_resend",
                args=[object_id],
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial = dict(initial) if initial else {}
        reply_to = request.GET.get("reply_to")
        if reply_to:
            try:
                message = (
                    NetMessage.objects.select_related("node_origin__role")
                    .get(pk=reply_to)
                )
            except (NetMessage.DoesNotExist, ValueError, TypeError):
                message = None
            if message:
                subject = (message.subject or "").strip()
                if subject:
                    if not subject.lower().startswith("re:"):
                        subject = f"Re: {subject}"
                else:
                    subject = "Re:"
                initial.setdefault("subject", subject[:64])
                if message.node_origin and "filter_node" not in initial:
                    initial["filter_node"] = message.node_origin.pk
        return initial

    def send_messages(self, request, queryset):
        for msg in queryset:
            msg.propagate()
        self.message_user(request, f"{queryset.count()} messages sent")

    send_messages.short_description = "Send selected messages"

    @admin.display(description="Role", ordering="filter_node_role")
    def filter_node_role_display(self, obj):
        return obj.filter_node_role

    @admin.display(description="TL", ordering="target_limit")
    def target_limit_display(self, obj):
        return obj.target_limit or ""

    @admin.display(description=_("Node Origin"), ordering="node_origin__name")
    def node_origin_display(self, obj):
        return obj.node_origin or ""

    @admin.display(description=_("Created"), ordering="created")
    def created_date_display(self, obj):
        created = obj.created
        if not created:
            return ""
        return timezone.localtime(created).date().isoformat()


