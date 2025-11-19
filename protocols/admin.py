from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from core.admin import EntityModelAdmin

from .models import CPForwarder, MediaBucket, MediaFile


class MediaFileInline(admin.TabularInline):
    model = MediaFile
    extra = 0
    readonly_fields = ("download", "original_name", "content_type", "size", "uploaded_at")
    fields = ("download", "original_name", "content_type", "size", "uploaded_at")

    @admin.display(description=_("File"))
    def download(self, obj):
        if obj and obj.file:
            return format_html('<a href="{url}" download>{name}</a>', url=obj.file.url, name=obj.original_name or obj.file.name)
        return ""


@admin.register(MediaBucket)
class MediaBucketAdmin(EntityModelAdmin):
    list_display = ("name", "slug", "expires_at", "is_active", "max_bytes", "file_count")
    search_fields = ("name", "slug")
    readonly_fields = ("upload_endpoint", "created_at", "updated_at")
    inlines = [MediaFileInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "upload_endpoint",
                    "allowed_patterns",
                    "max_bytes",
                    "expires_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description=_("Upload endpoint"))
    def upload_endpoint(self, obj):
        if not obj or not obj.pk:
            return ""
        path = reverse("protocols:media-bucket-upload", kwargs={"slug": obj.slug})
        return format_html('<code>{}</code>', path)

    @admin.display(boolean=True, description=_("Active"))
    def is_active(self, obj):
        return not obj.is_expired()

    @admin.display(description=_("Files"))
    def file_count(self, obj):
        return obj.files.count()


@admin.register(MediaFile)
class MediaFileAdmin(EntityModelAdmin):
    list_display = ("original_name", "bucket", "size", "uploaded_at")
    search_fields = ("original_name", "bucket__name", "bucket__slug")
    readonly_fields = ("file_link", "original_name", "content_type", "size", "uploaded_at")
    fields = ("bucket", "file_link", "original_name", "content_type", "size", "uploaded_at")
    autocomplete_fields = ("bucket",)

    @admin.display(description=_("File"))
    def file_link(self, obj):
        if obj and obj.file:
            return format_html('<a href="{url}" download>{name}</a>', url=obj.file.url, name=obj.original_name or obj.file.name)
        return ""


class CPForwarderForm(forms.ModelForm):
    forwarded_messages = forms.MultipleChoiceField(
        label=_("Forwarded messages"),
        choices=[
            (message, message)
            for message in CPForwarder.available_forwarded_messages()
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_(
            "Choose which OCPP messages should be forwarded. Only charge points "
            "with Export transactions enabled are eligible."
        ),
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
                "description": _(
                    "Only charge points with Export transactions enabled will be "
                    "forwarded by this configuration."
                ),
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
