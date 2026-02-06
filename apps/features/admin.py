from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from .models import Feature, FeatureNote, FeatureTest


class FeatureTestInline(admin.TabularInline):
    model = FeatureTest
    extra = 0
    fields = ("name", "node_id", "is_regression_guard", "notes")


class FeatureNoteInline(admin.TabularInline):
    model = FeatureNote
    extra = 0
    fields = ("author", "body", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(Feature)
class FeatureAdmin(OwnableAdminMixin, EntityModelAdmin):
    list_display = (
        "display",
        "slug",
        "is_enabled",
        "main_app",
        "node_feature",
        "owner_label",
    )
    list_filter = ("is_enabled", "main_app", "node_feature")
    search_fields = ("display", "slug", "summary")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "summary",
                    "is_enabled",
                    "main_app",
                    "node_feature",
                )
            },
        ),
        (
            _("Ownership"),
            {"fields": ("user", "group")},
        ),
        (
            _("Feature surfaces"),
            {
                "fields": (
                    "admin_requirements",
                    "public_requirements",
                    "service_requirements",
                    "admin_views",
                    "public_views",
                    "service_views",
                )
            },
        ),
        (
            _("Coverage"),
            {
                "fields": (
                    "code_locations",
                    "protocol_coverage",
                )
            },
        ),
    )
    inlines = [FeatureNoteInline, FeatureTestInline]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:feature_id>/toggle/",
                self.admin_site.admin_view(self.toggle_feature),
                name="features_feature_toggle",
            ),
        ]
        return custom_urls + urls

    def toggle_feature(self, request, feature_id: int):
        feature = self.get_object(request, feature_id)
        if feature is None:
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))
        if not self.has_change_permission(request, obj=feature):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:features_feature_change", args=[feature.pk]))

        feature.is_enabled = not feature.is_enabled
        feature.save(update_fields=["is_enabled", "updated_at"])
        status = _("enabled") if feature.is_enabled else _("disabled")
        messages.success(
            request,
            _("%(feature)s is now %(status)s.")
            % {"feature": feature.display, "status": status},
        )
        redirect_to = request.META.get("HTTP_REFERER")
        if redirect_to:
            return HttpResponseRedirect(redirect_to)
        return HttpResponseRedirect(reverse("admin:features_feature_changelist"))
