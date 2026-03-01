from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _, ngettext
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from .models import Feature, FeatureNote, FeatureTest


def _autogrow_textarea_widget() -> forms.Textarea:
    """Return a compact textarea widget that grows automatically in the browser."""

    return forms.Textarea(attrs={"rows": 1, "class": "feature-admin-autogrow"})


class FeatureTestInline(admin.TabularInline):
    model = FeatureTest
    extra = 0
    fields = ("name", "node_id", "is_regression_guard", "notes")
    formfield_overrides = {
        models.TextField: {"widget": _autogrow_textarea_widget()},
    }


class FeatureNoteInline(admin.TabularInline):
    model = FeatureNote
    extra = 0
    fields = ("author", "body", "updated_at")
    readonly_fields = ("updated_at",)
    formfield_overrides = {
        models.TextField: {"widget": _autogrow_textarea_widget()},
    }


@admin.register(Feature)
class FeatureAdmin(OwnableAdminMixin, DjangoObjectActions, EntityModelAdmin):
    change_list_template = "django_object_actions/change_list.html"
    changelist_actions = ("reload_base",)
    actions = ("toggle_selected_feature",)

    list_display = (
        "display",
        "slug",
        "source",
        "is_enabled",
        "main_app",
        "node_feature",
        "owner_label",
    )
    list_filter = ("source", "is_enabled", "main_app", "node_feature")
    search_fields = ("display", "slug", "summary")
    readonly_fields = ("source",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "source",
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
    formfield_overrides = {
        models.TextField: {"widget": _autogrow_textarea_widget()},
        models.JSONField: {"widget": _autogrow_textarea_widget()},
    }

    class Media:
        js = ("features/admin/feature_admin_autogrow.js",)

    def _mainstream_fixture_paths(self) -> list[Path]:
        """Return fixture files used to seed mainstream suite features."""

        fixtures_dir = Path(settings.BASE_DIR) / "apps" / "features" / "fixtures"
        return sorted(fixtures_dir.glob("features__*.json"))

    def _reload_all_preview_context(self) -> dict[str, object]:
        """Build the preview context for the reload-all confirmation view."""

        fixture_paths = self._mainstream_fixture_paths()
        feature_manager = getattr(self.model, "all_objects", self.model._default_manager)
        active_feature_count = feature_manager.filter(is_deleted=False).count()
        fixture_names = [path.name for path in fixture_paths]
        return {
            "fixture_paths": fixture_paths,
            "fixture_names": fixture_names,
            "active_feature_count": active_feature_count,
            "fixture_count": len(fixture_paths),
            "opts": self.model._meta,
            "title": _("Confirm Reload All"),
        }

    def reload_base(self, request, queryset=None):
        """Preview and optionally reload all suite features from mainstream fixtures."""

        del queryset

        if not self.has_delete_permission(request):
            raise PermissionDenied

        preview_context = self._reload_all_preview_context()

        if request.method != "POST" or request.POST.get("confirm") != "yes":
            return TemplateResponse(
                request,
                "admin/features/feature/reload_all_confirmation.html",
                preview_context,
            )

        fixture_paths = preview_context["fixture_paths"]
        if not fixture_paths:
            self.message_user(request, _("No feature fixtures found."), level=messages.WARNING)
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

        deleted_count = preview_context["active_feature_count"]
        try:
            feature_manager = getattr(self.model, "all_objects", self.model._default_manager)
            with transaction.atomic():
                feature_manager.update(is_seed_data=False)
                feature_manager.all().delete()
                call_command("load_user_data", *(str(path) for path in fixture_paths), verbosity=0)
        except CommandError as exc:
            self.message_user(
                request,
                _("Failed to reload fixtures: %(error)s") % {"error": exc},
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

        self.message_user(
            request,
            ngettext(
                "Dropped %(count)d suite feature before full reload.",
                "Dropped %(count)d suite features before full reload.",
                deleted_count,
            )
            % {"count": deleted_count},
            level=messages.SUCCESS,
        )
        self.message_user(
            request,
            ngettext(
                "Reloaded %(count)d mainstream fixture.",
                "Reloaded %(count)d mainstream fixtures.",
                len(fixture_paths),
            )
            % {"count": len(fixture_paths)},
            level=messages.SUCCESS,
        )

        return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

    reload_base.label = _("Reload All")
    reload_base.short_description = _("Reload All")
    reload_base.requires_queryset = False
    reload_base.methods = ("GET", "POST")

    @admin.action(description=_("Toggle selected feature"))
    def toggle_selected_feature(self, request, queryset):
        """Flip the enabled state for each selected suite feature."""

        toggled_total = 0
        enabled_total = 0
        disabled_total = 0

        with transaction.atomic():
            for feature in queryset.only("pk", "is_enabled"):
                feature.is_enabled = not feature.is_enabled
                feature.save(update_fields=["is_enabled", "updated_at"])
                toggled_total += 1
                if feature.is_enabled:
                    enabled_total += 1
                else:
                    disabled_total += 1

        self.message_user(
            request,
            ngettext(
                "Toggled %(count)d suite feature (%(enabled)d enabled, %(disabled)d disabled).",
                "Toggled %(count)d suite features (%(enabled)d enabled, %(disabled)d disabled).",
                toggled_total,
            )
            % {
                "count": toggled_total,
                "enabled": enabled_total,
                "disabled": disabled_total,
            },
            level=messages.SUCCESS,
        )

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
        if redirect_to and url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseRedirect(redirect_to)
        return HttpResponseRedirect(reverse("admin:features_feature_changelist"))
