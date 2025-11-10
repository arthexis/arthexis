from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from core.admin import EntityModelAdmin

from .models import CPForwarder


class CPForwarderForm(forms.ModelForm):
    forwarded_messages = forms.MultipleChoiceField(
        label=_("Forwarded messages"),
        choices=[
            (message, message)
            for message in CPForwarder.available_forwarded_messages()
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_("Choose which OCPP messages should be forwarded."),
    )

    class Meta:
        model = CPForwarder
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = CPForwarder.available_forwarded_messages()
        if self.instance and self.instance.pk:
            initial = self.instance.get_forwarded_messages()
        self.fields["forwarded_messages"].initial = initial

    def clean_forwarded_messages(self):
        selected = self.cleaned_data.get("forwarded_messages") or []
        return CPForwarder.sanitize_forwarded_messages(selected)


@admin.register(CPForwarder)
class CPForwarderAdmin(EntityModelAdmin):
    form = CPForwarderForm
    list_display = (
        "display_name",
        "target_node",
        "enabled",
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
    )
    list_filter = ("enabled", "is_running", "target_node")
    search_fields = (
        "name",
        "target_node__hostname",
        "target_node__public_endpoint",
        "target_node__address",
    )
    autocomplete_fields = ["target_node", "source_node"]
    readonly_fields = (
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    actions = ["test_forwarders"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "source_node",
                    "target_node",
                    "enabled",
                    "is_running",
                    "last_forwarded_at",
                    "last_status",
                    "last_error",
                    "last_synced_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            _("Forwarding"),
            {
                "classes": ("collapse",),
                "fields": ("forwarded_messages",),
            },
        ),
    )

    @admin.display(description=_("Name"))
    def display_name(self, obj):
        if obj.name:
            return obj.name
        if obj.target_node:
            return str(obj.target_node)
        return _("Forwarder")

    @admin.action(description=_("Test forwarder configuration"))
    def test_forwarders(self, request, queryset):
        tested = 0
        for forwarder in queryset:
            forwarder.sync_chargers()
            tested += 1
        if tested:
            self.message_user(
                request,
                _("Tested %(count)s forwarder(s).") % {"count": tested},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
