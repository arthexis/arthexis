from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin.mixins import PublicViewLinksAdminMixin
from apps.links.admin import ReferenceAttachmentAdminMixin
from apps.media.models import MediaFile

from .models import (
    RegistrationSubmission,
    Term,
    TermAcceptance,
    ensure_terms_document_bucket,
)


@admin.register(Term)
class TermAdmin(
    PublicViewLinksAdminMixin, ReferenceAttachmentAdminMixin, admin.ModelAdmin
):
    list_display = (
        "title",
        "slug",
        "category",
        "security_group",
        "reference_preview",
        "updated_at",
    )
    list_filter = ("category", "security_group")
    search_fields = ("title", "slug")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "slug",
                    "summary",
                    "document_media",
                    "body_text",
                    "checkbox_text",
                )
            },
        ),
        (
            _("Publication"),
            {
                "fields": (
                    "category",
                    "security_group",
                )
            },
        ),
        (
            _("Required Document"),
            {
                "fields": (
                    "requires_document",
                    "required_document_label",
                    "required_document_patterns",
                    "required_document_min_bytes",
                    "required_document_max_bytes",
                )
            },
        ),
        (
            _("Reference"),
            {
                "fields": ("reference",),
            },
        ),
    )
    view_on_site = False

    def _user_can_view_public_term(self, request, obj: Term | None) -> bool:
        """Return whether the current admin user can open the term detail route."""

        if obj is None:
            return False
        if obj.category != Term.Category.SECURITY_GROUP:
            return True
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        if user.is_superuser:
            return True
        if not obj.security_group_id:
            return False
        return user.groups.filter(pk=obj.security_group_id).exists()

    def get_public_view_links(self, obj=None, request=None) -> list[dict[str, str]]:
        """Return public term routes relevant to term administration."""

        links = [{"label": "View on site: Registration", "url": reverse("terms:registration")}]
        if self._user_can_view_public_term(request, obj):
            links.append({"label": "View on site: Term", "url": obj.get_absolute_url()})
        return links

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        bucket = ensure_terms_document_bucket()
        form.base_fields["document_media"].queryset = MediaFile.objects.filter(
            bucket=bucket
        )
        return form

    def reference_preview(self, obj):
        if not obj.reference_id:
            return ""
        if not obj.reference.image_url:
            return obj.reference.value
        return format_html(
            '<img src="{}" alt="{}" style="width: 64px; height: 64px;" />',
            obj.reference.image_url,
            obj.reference.alt_text,
        )

    reference_preview.short_description = _("Reference")


@admin.register(TermAcceptance)
class TermAcceptanceAdmin(admin.ModelAdmin):
    list_display = (
        "term",
        "user",
        "submission",
        "accepted_at",
    )
    list_filter = ("term",)
    search_fields = ("user__username", "user__email")
    readonly_fields = (
        "term",
        "user",
        "submission",
        "accepted_at",
        "checkbox_text",
        "ip_address",
        "user_agent",
        "required_document_media",
    )


@admin.register(RegistrationSubmission)
class RegistrationSubmissionAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "submitted_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("user__username", "user__email")
    readonly_fields = ("submitted_at",)
