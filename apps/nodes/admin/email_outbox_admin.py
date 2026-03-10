from django.contrib import admin, messages
from django.db.models import Max
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.core.admin import EmailOutboxAdminForm, OwnableAdminMixin
from apps.core.admin.metrics import annotate_enabled_total, format_enabled_total, max_attr
from apps.emails.models import EmailOutbox
from apps.locals.entity import EntityModelAdmin


class EmailOutboxAdmin(OwnableAdminMixin, EntityModelAdmin):
    form = EmailOutboxAdminForm
    actions = ["test_outboxes"]
    list_display = (
        "username",
        "owner_label",
        "collector_count",
        "last_used_at",
        "host",
        "port",
        "use_tls",
        "use_ssl",
        "is_enabled",
    )
    change_form_template = "admin/nodes/emailoutbox/change_form.html"
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Configuration",
            {
                "fields": (
                    "node",
                    "host",
                    "port",
                    "use_tls",
                    "use_ssl",
                    "from_email",
                    "is_enabled",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = annotate_enabled_total(
            super().get_queryset(request),
            "bridge__inbox__collectors",
            total_alias="total_collectors",
            enabled_alias="enabled_collectors",
        )
        return queryset.annotate(
            last_outbox_transaction_at=Max("transactions__processed_at"),
            last_inbox_transaction_at=Max("bridge__inbox__transactions__processed_at"),
        )

    @admin.display(description=_("Collectors"), ordering="enabled_collectors")
    def collector_count(self, obj):
        return format_enabled_total(
            obj,
            enabled_attr="enabled_collectors",
            total_attr="total_collectors",
        )

    @admin.display(description=_("Last used"))
    def last_used_at(self, obj):
        return max_attr(obj, "last_outbox_transaction_at", "last_inbox_transaction_at") or "-"

    @admin.action(description="Test selected Outbox")
    def test_outboxes(self, request, queryset):
        recipient = request.user.email
        for outbox in queryset:
            target = recipient or outbox.username
            if not target:
                self.message_user(
                    request, f"{outbox}: No recipient available", level=messages.ERROR
                )
                continue

            try:
                outbox.send_mail(
                    "Test email",
                    "This is a test email.",
                    [target],
                )
                self.message_user(
                    request, f"{outbox}: Test email sent", level=messages.SUCCESS
                )
            except Exception as exc:  # pragma: no cover - admin feedback
                self.message_user(request, f"{outbox}: {exc}", level=messages.ERROR)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_outbox),
                name="nodes_emailoutbox_test",
            )
        ]
        return custom + urls

    def test_outbox(self, request, object_id):
        outbox = self.get_object(request, object_id)
        if not outbox:
            self.message_user(request, "Unknown outbox", messages.ERROR)
            return redirect("..")
        recipient = request.user.email or outbox.username
        try:
            outbox.send_mail(
                "Test email",
                "This is a test email.",
                [recipient],
            )
            self.message_user(request, "Test email sent", messages.SUCCESS)
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(request, str(exc), messages.ERROR)
        return redirect("..")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["test_url"] = reverse(
                "admin:nodes_emailoutbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)
