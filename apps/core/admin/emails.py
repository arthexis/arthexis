from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.shortcuts import redirect
from django.template import TemplateDoesNotExist
from django.template.loader import select_template
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.emails.models import EmailCollector, EmailInbox
from apps.locals.user_data import EntityModelAdmin

from .forms import EmailInboxAdminForm
from .inlines import EmailCollectorInline
from .metrics import annotate_enabled_total, format_enabled_total
from .mixins import OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction


SETUP_COLLECTOR_TEXT = _("Setup Collector")


class EmailCollectorAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "inbox",
        "is_enabled",
        "subject",
        "sender",
        "body",
        "fragment",
        "notification_mode",
    )
    list_filter = ("is_enabled", "notification_mode")
    search_fields = (
        "name",
        "subject",
        "sender",
        "body",
        "fragment",
        "notification_subject",
        "notification_message",
        "notification_recipients",
        "notification_recipe__slug",
        "notification_recipe__display",
    )
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
        try:
            template_name = select_template(
                [
                    f"admin/{self.model._meta.app_label}/{self.model._meta.model_name}/preview.html",
                    "admin/core/emailcollector/preview.html",
                ]
            ).template.name
        except TemplateDoesNotExist:
            self.message_user(
                request,
                _("Preview template is not configured for Email Collectors."),
                messages.ERROR,
            )
            changelist_url = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"
            )
            return redirect(changelist_url)

        return TemplateResponse(request, template_name, context)


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


class EmailCollectorSetupForm(forms.ModelForm):
    """Wizard-style setup form used by the inbox admin tool action."""

    test_now = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Run a live test after saving"),
    )

    class Meta:
        model = EmailCollector
        fields = (
            "name",
            "subject",
            "sender",
            "body",
            "fragment",
            "use_regular_expressions",
            "notification_mode",
            "notification_subject",
            "notification_message",
            "notification_recipients",
            "notification_recipe",
            "additional_inboxes",
        )


class EmailInboxAdmin(
    OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin
):
    form = EmailInboxAdminForm
    list_display = (
        "username",
        "owner_label",
        "collector_count",
        "last_used_at",
        "host",
        "protocol",
        "is_enabled",
    )
    actions = ["test_connection", "search_inbox", "test_collectors"]
    change_actions = [
        "setup_collector_action",
        "test_collectors_action",
        "my_profile_action",
    ]
    changelist_actions = ["setup_collector", "my_profile"]
    change_form_template = "admin/core/emailinbox/change_form.html"
    inlines = [EmailCollectorInline]

    def get_queryset(self, request):
        queryset = annotate_enabled_total(
            super().get_queryset(request),
            "collectors",
            total_alias="total_collectors",
            enabled_alias="enabled_collectors",
        )
        return queryset.annotate(last_transaction_at=Max("transactions__processed_at"))

    @admin.display(description=_("Collectors"), ordering="enabled_collectors")
    def collector_count(self, obj):
        return format_enabled_total(
            obj,
            enabled_attr="enabled_collectors",
            total_attr="total_collectors",
        )

    @admin.display(description=_("Last used"), ordering="last_transaction_at")
    def last_used_at(self, obj):
        return obj.last_transaction_at or "-"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_inbox),
                name="emails_emailinbox_test",
            ),
            path(
                "<path:object_id>/collector-setup/",
                self.admin_site.admin_view(self.setup_collector_view),
                name="emails_emailinbox_setup_collector",
            ),
        ]
        return custom + urls

    def _setup_collector_url(self, inbox) -> str:
        """Return the setup collector URL for the provided inbox."""

        return reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk])

    @admin.action(description=_("Setup Collector"))
    def setup_collector(self, request, queryset=None):
        """Open the collector setup wizard for a selected inbox."""

        selected_ids = request.POST.getlist("_selected_action")
        if len(selected_ids) > 1:
            self.message_user(request, _("Select exactly one inbox to start setup."), messages.ERROR)
            return redirect(reverse("admin:emails_emailinbox_changelist"))

        inbox = None
        if len(selected_ids) == 1:
            inbox = EmailInbox.objects.filter(pk=selected_ids[0]).first()
        elif queryset is not None:
            inbox = queryset.first()

        if inbox is None:
            self.message_user(request, _("Select one inbox to start setup."), messages.ERROR)
            return redirect(reverse("admin:emails_emailinbox_changelist"))
        return redirect(self._setup_collector_url(inbox))

    setup_collector.label = _("Setup Collector")
    setup_collector.short_description = _("Setup Collector")
    setup_collector.requires_queryset = False

    def setup_collector_action(self, request, obj):
        """Open the collector setup wizard from the inbox change form."""

        return redirect(self._setup_collector_url(obj))

    setup_collector_action.label = _("Setup Collector")
    setup_collector_action.short_description = _("Setup Collector")

    def setup_collector_view(self, request, object_id):
        """Render and process the interactive collector setup wizard."""

        inbox = self.get_object(request, object_id)
        if not inbox:
            self.message_user(request, _("Unknown inbox."), messages.ERROR)
            return redirect("..")

        collector = inbox.collectors.order_by("id").first() or EmailCollector(inbox=inbox)
        results = []
        if request.method == "POST":
            form = EmailCollectorSetupForm(request.POST, instance=collector)
            form.fields["additional_inboxes"].queryset = EmailInbox.objects.exclude(pk=inbox.pk)
            if form.is_valid():
                configured_collector = form.save(commit=False)
                configured_collector.inbox = inbox
                configured_collector.save()
                form.save_m2m()
                if form.cleaned_data.get("test_now"):
                    try:
                        results = configured_collector.search_messages(limit=5)
                        if results:
                            self.message_user(request, _("Collector test found matching emails."), messages.SUCCESS)
                        else:
                            self.message_user(request, _("Collector test found no matching emails."), messages.WARNING)
                    except ValidationError as exc:
                        self.message_user(request, str(exc), messages.ERROR)
                    except Exception as exc:  # pragma: no cover - admin feedback
                        self.message_user(request, str(exc), messages.ERROR)
                collector = configured_collector
        else:
            form = EmailCollectorSetupForm(instance=collector)
            form.fields["additional_inboxes"].queryset = EmailInbox.objects.exclude(pk=inbox.pk)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": inbox,
            "title": _("Setup Collector"),
            "form": form,
            "collector": collector,
            "results": results,
            "change_url": reverse("admin:emails_emailinbox_change", args=[inbox.pk]),
        }
        return TemplateResponse(request, "admin/core/emailinbox/setup_collector.html", context)

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
        """Inject admin utility links into the inbox change form context."""

        extra_context = extra_context or {}
        if object_id:
            extra_context["test_url"] = reverse(
                "admin:emails_emailinbox_test", args=[object_id]
            )
            extra_context["setup_collector_url"] = reverse(
                "admin:emails_emailinbox_setup_collector", args=[object_id]
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
        for collector in inbox.collectors.filter(is_enabled=True):
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

    @admin.action(description="Search target inboxes")
    def search_inbox(self, request, queryset):
        if request.POST.get("apply"):
            form = EmailSearchForm(request.POST)
            if form.is_valid():
                results = []
                for inbox in queryset:
                    messages = inbox.search_messages(
                        subject=form.cleaned_data["subject"].replace("\r", "").replace("\n", ""),
                        from_address=form.cleaned_data["from_address"].replace("\r", "").replace("\n", ""),
                        body=form.cleaned_data["body"].replace("\r", "").replace("\n", ""),
                        use_regular_expressions=False,
                    )
                    results.append({"inbox": inbox, "messages": messages})
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
