from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import Landing, LandingLead
from ..utils import landing_leads_supported


@admin.register(Landing)
class LandingAdmin(EntityModelAdmin):
    list_display = (
        "label",
        "path",
        "module",
        "enabled",
        "track_leads",
        "validation_status",
    )
    list_filter = (
        "enabled",
        "track_leads",
        "module__roles",
        "module__application",
    )
    search_fields = (
        "label",
        "path",
        "description",
        "module__path",
        "module__application__name",
    )
    fields = (
        "module",
        "path",
        "label",
        "enabled",
        "track_leads",
        "description",
        "validation_status",
        "validated_url_at",
    )
    readonly_fields = ("validation_status", "validated_url_at")
    list_select_related = ("module", "module__application")


@admin.register(LandingLead)
class LandingLeadAdmin(EntityModelAdmin):
    list_display = (
        "landing_label",
        "landing_path",
        "status",
        "user",
        "referer_display",
        "created_on",
    )
    list_filter = (
        "status",
        "landing__module__roles",
        "landing__module__application",
    )
    search_fields = (
        "landing__label",
        "landing__path",
        "referer",
        "path",
        "user__username",
        "user__email",
    )
    readonly_fields = (
        "landing",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
    )
    fields = (
        "landing",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "status",
        "assign_to",
        "created_on",
    )
    list_select_related = ("landing", "landing__module", "landing__module__application")
    ordering = ("-created_on",)
    date_hierarchy = "created_on"

    def changelist_view(self, request, extra_context=None):
        if not landing_leads_supported():
            self.message_user(
                request,
                _(
                    "Landing leads are not being recorded because Celery is not running on this node."
                ),
                messages.WARNING,
            )
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description=_("Landing"), ordering="landing__label")
    def landing_label(self, obj):
        return obj.landing.label

    @admin.display(description=_("Path"), ordering="landing__path")
    def landing_path(self, obj):
        return obj.landing.path

    @admin.display(description=_("Referrer"))
    def referer_display(self, obj):
        return obj.referer or ""
