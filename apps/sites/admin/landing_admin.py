from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from ..models import Landing, ReferrerLanding


@admin.register(Landing)
class LandingAdmin(EntityModelAdmin):
    list_display = (
        "label",
        "path",
        "module",
        "enabled",
        "validation_status",
    )
    list_filter = (
        "enabled",
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
        "description",
        "agent_notes",
        "validation_status",
        "validated_url_at",
    )
    readonly_fields = ("validation_status", "validated_url_at")
    list_select_related = ("module", "module__application")


@admin.register(ReferrerLanding)
class ReferrerLandingAdmin(EntityModelAdmin):
    list_display = ("referrer_domain", "site", "landing", "enabled")
    list_filter = ("enabled",)
    search_fields = (
        "referrer_domain",
        "landing__label",
        "landing__path",
        "site__domain",
    )
    fields = ("site", "referrer_domain", "landing", "enabled", "description")
    list_select_related = ("landing", "site")
