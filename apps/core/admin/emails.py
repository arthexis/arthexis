from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.emails.models import EmailCollector, EmailInbox
from apps.locals.user_data import EntityModelAdmin

from .forms import EmailInboxAdminForm
from .inlines import EmailCollectorInline
from .mixins import OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction


class EmailCollectorAdmin(EntityModelAdmin):
    list_display = ("name", "inbox", "subject", "sender", "body", "fragment")
    search_fields = ("name", "subject", "sender", "body", "fragment")
    actions = ["preview_messages"]

    @admin.action(description=_("Preview matches"))
    def preview_messages(self, request, queryset):
        results = []
        for collector in queryset.select_related("inbox"):
            try:
                messages_list = collector.search_messages(limit=5)
                error = None
            except ValidationError as exc:
                messages_list = []
                error = str(exc)
            except Exception as exc:  # pragma: no cover - admin feedback
                messages_list = []
                error = str(exc)
            results.append(
                {
                    "collector": collector,
                    "messages": messages_list,
                    "error": error,
                }
            )
        context = {
            "title": _("Preview Email Collectors"),
            "results": results,
            "opts": self.model._meta,
            "queryset": queryset,
        }
        return TemplateResponse(
            request, "admin/core/emailcollector/preview.html", context
        )


class EmailSearchForm(forms.Form):
    subject = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"style": "width: 40em;"})
    )
    from_address = forms.CharField(
        label="From",
        required=False,
        widget=forms.TextInput(attrs={"style": "width: 40em;"}),
    )
    body = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"style": "width: 40em; height: 10em;"}),
    )


class EmailInboxAdmin(
    OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin
):
    form = EmailInboxAdminForm
    list_display = ("owner_label", "username", "host", "protocol", "is_enabled")
    actions = ["test_connection", "search_inbox", "test_collectors"]
    change_actions = ["test_collectors_action", "my_profile_action"]
    changelist_actions = ["my_profile"]
    change_form_template = "admin/core/emailinbox/change_form.html"
    inlines = [EmailCollectorInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_inbox),
                name="emails_emailinbox_test",
            )
        ]
        return custom + urls

    def test_inbox(self, request, object_id):
        inbox = self.get_object(request, object_id)
        if not inbox:
            self.message_user(request, "Unknown inbox", messages.ERROR)
            return redirect("..")
        try:
            inbox.test_connection()
            self.message_user(request, "Inbox connection successful", messages.SUCCESS)
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(request, str(exc), messages.ERROR)
        return redirect("..")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["test_url"] = reverse(
                "admin:emails_emailinbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Configuration",
            {"fields": ("host", "port", "protocol", "use_ssl", "is_enabled", "priority")},
        ),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    @admin.action(description="Test selected inboxes")
    def test_connection(self, request, queryset):
        for inbox in queryset:
            try:
                inbox.test_connection()
                self.message_user(request, f"{inbox} connection successful")
            except Exception as exc:  # pragma: no cover - admin feedback
                self.message_user(request, f"{inbox}: {exc}", level=messages.ERROR)

    def _test_collectors(self, request, inbox):
        for collector in inbox.collectors.all():
            before = collector.artifacts.count()
            try:
                collector.collect(limit=1)
                after = collector.artifacts.count()
                if after > before:
                    msg = f"{collector} collected {after - before} email(s)"
                    self.message_user(request, msg)
                else:
                    self.message_user(
                        request, f"{collector} found no emails", level=messages.WARNING
                    )
            except Exception as exc:  # pragma: no cover - admin feedback
                self.message_user(request, f"{collector}: {exc}", level=messages.ERROR)

    @admin.action(description="Test collectors")
    def test_collectors(self, request, queryset):
        for inbox in queryset:
            self._test_collectors(request, inbox)

    def test_collectors_action(self, request, obj):
        self._test_collectors(request, obj)

    test_collectors_action.label = "Test collectors"
    test_collectors_action.short_description = "Test collectors"

    @admin.action(description="Search selected inbox")
    def search_inbox(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Please select exactly one inbox.", level=messages.ERROR
            )
            return None
        inbox = queryset.first()
        if request.POST.get("apply"):
            form = EmailSearchForm(request.POST)
            if form.is_valid():
                results = inbox.search_messages(
                    subject=form.cleaned_data["subject"],
                    from_address=form.cleaned_data["from_address"],
                    body=form.cleaned_data["body"],
                    use_regular_expressions=False,
                )
                context = {
                    "form": form,
                    "results": results,
                    "queryset": queryset,
                    "action": "search_inbox",
                    "opts": self.model._meta,
                }
                return TemplateResponse(
                    request, "admin/core/emailinbox/search.html", context
                )
        else:
            form = EmailSearchForm()
        context = {
            "form": form,
            "queryset": queryset,
            "action": "search_inbox",
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/core/emailinbox/search.html", context)
