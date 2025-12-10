import json
import uuid

from django.contrib import admin
from django.http import JsonResponse
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.csrf import csrf_exempt

from apps.locals.user_data import EntityModelAdmin
from .models import ExperienceReference, Reference


@admin.register(ExperienceReference)
class ReferenceAdmin(EntityModelAdmin):
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
    )
    fields = (
        "alt_text",
        "content_type",
        "value",
        "file",
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
        if obj.image:
            return format_html(
                '<img src="{}" alt="{}" style="height:200px;"/>',
                obj.image.url,
                obj.alt_text,
            )
        return ""

    qr_code.short_description = "QR Code"
