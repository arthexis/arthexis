from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _, ngettext
from django_object_actions import DjangoObjectActions

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
class FeatureAdmin(OwnableAdminMixin, DjangoObjectActions, EntityModelAdmin):
    change_list_template = "django_object_actions/change_list.html"
    changelist_actions = ("reload_base",)

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

    def _mainstream_fixture_paths(self) -> list[Path]:
        """Return fixture files used to seed mainstream suite features."""

        fixtures_dir = Path(settings.BASE_DIR) / "apps" / "features" / "fixtures"
        return sorted(fixtures_dir.glob("features__*.json"))

    def reload_base(self, request, queryset=None):
        """Drop all suite features and reload only mainstream fixture entries."""

        del queryset

        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

        fixture_paths = self._mainstream_fixture_paths()
        if not fixture_paths:
            self.message_user(request, _("No feature fixtures found."), level=messages.WARNING)
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

        feature_manager = getattr(self.model, "all_objects", self.model._default_manager)
        deleted_count = feature_manager.filter(is_deleted=False).count()
        feature_manager.update(is_seed_data=False)
        feature_manager.all().delete()

        loaded = 0
        for fixture_path in fixture_paths:
            try:
                call_command("load_user_data", str(fixture_path), verbosity=0)
            except CommandError as exc:
                self.message_user(
                    request,
                    _("%(fixture)s: %(error)s") % {"fixture": fixture_path.name, "error": exc},
                    level=messages.ERROR,
                )
            else:
                loaded += 1

        self.message_user(
            request,
            ngettext(
                "Dropped %(count)d suite feature before base reload.",
                "Dropped %(count)d suite features before base reload.",
                deleted_count,
            )
            % {"count": deleted_count},
            level=messages.SUCCESS,
        )
        if loaded:
            self.message_user(
                request,
                ngettext(
                    "Reloaded %(count)d mainstream fixture.",
                    "Reloaded %(count)d mainstream fixtures.",
                    loaded,
                )
                % {"count": loaded},
                level=messages.SUCCESS,
            )

        return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

    reload_base.label = _("Reload Base")
    reload_base.short_description = _("Reload Base")
    reload_base.requires_queryset = False

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
