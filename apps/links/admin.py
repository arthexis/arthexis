import json
import uuid

from django import forms
from django.contrib import admin
from django.http import JsonResponse
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from apps.locals.user_data import EntityModelAdmin
from apps.media.models import MediaFile
from apps.media.utils import create_media_file
from .models import (
    ExperienceReference,
    QRRedirect,
    QRRedirectLead,
    Reference,
    get_reference_file_bucket,
    get_reference_qr_bucket,
)


class ReferenceAdminForm(forms.ModelForm):
    file_upload = forms.FileField(required=False, label=_("File upload"))
    image_upload = forms.ImageField(required=False, label=_("Image upload"))

    class Meta:
        model = Reference
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file_media"].queryset = MediaFile.objects.filter(
            bucket=self._get_file_bucket()
        )
        self.fields["image_media"].queryset = MediaFile.objects.filter(
            bucket=self._get_qr_bucket()
        )

    def _get_file_bucket(self):
        if not hasattr(self, "_file_bucket"):
            self._file_bucket = get_reference_file_bucket()
        return self._file_bucket

    def _get_qr_bucket(self):
        if not hasattr(self, "_qr_bucket"):
            self._qr_bucket = get_reference_qr_bucket()
        return self._qr_bucket

    def _clean_upload(self, upload, bucket):
        if upload:
            if not bucket.allows_filename(upload.name):
                raise forms.ValidationError(_("File type is not allowed."))
            if not bucket.allows_size(upload.size):
                raise forms.ValidationError(_("File exceeds the allowed size."))
        return upload

    def save(self, commit=True):
        instance = super().save(commit=False)
        file_upload = self.cleaned_data.get("file_upload")
        if file_upload:
            instance.file_media = create_media_file(
                bucket=self._get_file_bucket(), uploaded_file=file_upload
            )
        image_upload = self.cleaned_data.get("image_upload")
        if image_upload:
            instance.image_media = create_media_file(
                bucket=self._get_qr_bucket(), uploaded_file=image_upload
            )
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_file_upload(self):
        upload = self.cleaned_data.get("file_upload")
        return self._clean_upload(upload, self._get_file_bucket())

    def clean_image_upload(self):
        upload = self.cleaned_data.get("image_upload")
        return self._clean_upload(upload, self._get_qr_bucket())


@admin.register(ExperienceReference)
class ReferenceAdmin(EntityModelAdmin):
    form = ReferenceAdminForm
    list_display = (
        "alt_text",
        "content_type",
        "link",
        "open_in_admin_frame",
        "header",
        "footer",
        "visibility",
        "validation_status",
        "validated_url_at",
        "author",
        "transaction_uuid",
    )
    readonly_fields = (
        "uses",
        "qr_code",
        "author",
        "validated_url_at",
        "validation_status",
        "file_metadata",
        "image_metadata",
    )
    fields = (
        "alt_text",
        "content_type",
        "value",
        "file_media",
        "file_upload",
        "file_metadata",
        "method",
        "validation_status",
        "validated_url_at",
        "roles",
        "features",
        "sites",
        "include_in_footer",
        "show_in_header",
        "footer_visibility",
        "transaction_uuid",
        "author",
        "uses",
        "qr_code",
        "image_media",
        "image_upload",
        "image_metadata",
    )
    filter_horizontal = ("roles", "features", "sites")

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj:
            ro.append("transaction_uuid")
        return ro

    @admin.display(description="Footer", boolean=True, ordering="include_in_footer")
    def footer(self, obj):
        return obj.include_in_footer

    @admin.display(description="Header", boolean=True, ordering="show_in_header")
    def header(self, obj):
        return obj.show_in_header

    @admin.display(description="Visibility", ordering="footer_visibility")
    def visibility(self, obj):
        return obj.get_footer_visibility_display()

    @admin.display(description="LINK")
    def link(self, obj):
        if obj.value:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">open</a>',
                obj.value,
            )
        return ""

    @admin.display(description="Open in Admin Frame")
    def open_in_admin_frame(self, obj):
        if obj.value:
            url = reverse("admin:links_reference_frame", args=[obj.pk])
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">open</a>',
                url,
            )
        return ""

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "bulk/",
                self.admin_site.admin_view(csrf_exempt(self.bulk_create)),
                name="links_reference_bulk",
            ),
            path(
                "<int:reference_id>/frame/",
                self.admin_site.admin_view(self.frame_view),
                name="links_reference_frame",
            ),
        ]
        return custom + urls

    def bulk_create(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "POST required"}, status=405)
        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        refs = payload.get("references", [])
        transaction_uuid = payload.get("transaction_uuid") or uuid.uuid4()
        created_ids = []
        for data in refs:
            ref = Reference.objects.create(
                alt_text=data.get("alt_text", ""),
                value=data.get("value", ""),
                transaction_uuid=transaction_uuid,
                author=request.user if request.user.is_authenticated else None,
            )
            created_ids.append(ref.id)
        return JsonResponse(
            {"transaction_uuid": str(transaction_uuid), "ids": created_ids}
        )

    def frame_view(self, request, reference_id):
        obj = self.get_object(request, reference_id)
        if obj is None:
            raise Http404("Reference does not exist")

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": obj,
            "title": str(obj),
            "iframe_url": obj.value,
        }

        return TemplateResponse(
            request,
            "admin/links/reference/frame.html",
            context,
        )

    def qr_code(self, obj):
        if obj.image_url:
            return format_html(
                '<img src="{}" alt="{}" style="height:200px;"/>',
                obj.image_url,
                obj.alt_text,
            )
        return ""

    qr_code.short_description = "QR Code"

    @admin.display(description=_("File metadata"))
    def file_metadata(self, obj):
        media = getattr(obj, "file_media", None)
        if not media:
            return _("No file uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }

    @admin.display(description=_("Image metadata"))
    def image_metadata(self, obj):
        media = getattr(obj, "image_media", None)
        if not media:
            return _("No image uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }


@admin.register(QRRedirect)
class QRRedirectAdmin(EntityModelAdmin):
    list_display = (
        "slug",
        "title",
        "target_url",
        "is_public",
        "public_view_link",
        "redirect_link",
        "created_on",
    )
    list_filter = ("is_public",)
    search_fields = ("slug", "title", "target_url")
    readonly_fields = ("created_on", "public_view_link", "redirect_link")
    fields = (
        "slug",
        "title",
        "target_url",
        "is_public",
        "text_above",
        "text_below",
        "public_view_link",
        "redirect_link",
        "created_on",
    )

    @admin.display(description="Public view")
    def public_view_link(self, obj):
        if not obj.slug:
            return ""
        url = reverse("links:qr-redirect-public", args=[obj.slug])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">open</a>',
            url,
        )

    @admin.display(description="Redirect URL")
    def redirect_link(self, obj):
        if not obj.slug:
            return ""
        url = reverse("links:qr-redirect", args=[obj.slug])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">open</a>',
            url,
        )


@admin.register(QRRedirectLead)
class QRRedirectLeadAdmin(EntityModelAdmin):
    list_display = (
        "qr_redirect",
        "status",
        "user",
        "referer_display",
        "created_on",
    )
    list_filter = ("status",)
    search_fields = (
        "qr_redirect__slug",
        "qr_redirect__title",
        "target_url",
        "referer",
        "path",
        "user__username",
        "user__email",
    )
    readonly_fields = (
        "qr_redirect",
        "target_url",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
    )
    fields = (
        "qr_redirect",
        "target_url",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "status",
        "assign_to",
        "created_on",
    )
    ordering = ("-created_on",)
    date_hierarchy = "created_on"

    @admin.display(description="Referrer")
    def referer_display(self, obj):
        return obj.referer or ""
