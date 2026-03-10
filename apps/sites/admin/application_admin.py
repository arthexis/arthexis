from django.contrib import admin, messages
from django.conf import settings
from django.utils.translation import gettext_lazy as _, ngettext

from apps.app.models import (
    Application,
    ApplicationModel,
    refresh_application_models,
)
from apps.links.models.reference import Reference
from apps.locals.entity import EntityModelAdmin
from utils.enabled_apps_lock import get_enabled_apps_lock_path

from .filters import ApplicationInstalledListFilter
from .forms import ApplicationForm


class ApplicationReferenceInline(admin.TabularInline):
    """Read-only reference links associated with the selected application."""

    model = Reference
    extra = 0
    can_delete = False
    fields = ("alt_text", "value", "include_in_footer", "show_in_header")
    readonly_fields = ("alt_text", "value", "include_in_footer", "show_in_header")
    ordering = ("alt_text",)
    verbose_name = _("Reference link")
    verbose_name_plural = _("Reference links")

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin UI
        return False


class ApplicationModelInline(admin.TabularInline):
    model = ApplicationModel
    extra = 0
    can_delete = False
    fields = ("label", "model_name", "verbose_name", "wiki_url")
    readonly_fields = ("label", "model_name", "verbose_name")
    ordering = ("label",)

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin UI
        return False


@admin.register(Application)
class ApplicationAdmin(EntityModelAdmin):
    form = ApplicationForm
    list_display = (
        "name",
        "order",
        "importance",
        "app_verbose_name",
        "description",
        "installed",
    )
    search_fields = ("name", "description")
    readonly_fields = ("installed",)
    inlines = (ApplicationModelInline, ApplicationReferenceInline)
    list_filter = (
        ApplicationInstalledListFilter,
        "order",
        "importance",
        "is_deleted",
        "is_seed_data",
        "is_user_data",
    )
    actions = ("discover_app_models",)

    def save_model(self, request, obj, form, change):
        """Persist enabled-app lock metadata after model changes."""

        super().save_model(request, obj, form, change)
        self._notify_restart_required(request)

    def delete_model(self, request, obj):
        """Persist enabled-app lock metadata after model deletion."""

        super().delete_model(request, obj)
        self._notify_restart_required(request)

    @admin.display(description="Verbose name")
    def app_verbose_name(self, obj):
        return obj.verbose_name

    @admin.display(boolean=True)
    def installed(self, obj):
        return obj.installed

    @admin.action(description=_("Discover App Models"))
    def discover_app_models(self, request, queryset):
        refresh_application_models(using=queryset.db, applications=queryset)
        self.message_user(
            request,
            ngettext(
                "Discovered models for %(count)d application.",
                "Discovered models for %(count)d applications.",
                queryset.count(),
            )
            % {"count": queryset.count()},
            level=messages.SUCCESS,
        )

    def _notify_restart_required(self, request) -> None:
        """Inform staff that app enablement takes effect after a suite restart."""

        lock_path = get_enabled_apps_lock_path(settings.BASE_DIR)
        self.message_user(
            request,
            _(
                "Application enablement changes are written to %(lock)s and apply on "
                "the next suite restart. Delete that file to re-enable all apps, or "
                "edit it offline to control enabled app labels."
            )
            % {"lock": lock_path},
            level=messages.WARNING,
        )
