from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from apps.app.models import Application, ApplicationModel, refresh_application_models
from apps.links.models import Reference
from apps.locals.user_data import EntityModelAdmin

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
