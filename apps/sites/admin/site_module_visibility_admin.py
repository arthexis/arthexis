from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin
from apps.sites.models import SiteModuleVisibility


@admin.register(SiteModuleVisibility)
class SiteModuleVisibilityAdmin(EntityModelAdmin):
    list_display = ("site", "module", "visibility", "audience", "is_enabled")
    list_filter = ("visibility", "audience", "is_enabled", "site")
    list_select_related = ("site", "module", "module__application")
    search_fields = (
        "site__domain",
        "site__name",
        "module__menu",
        "module__path",
        "module__application__name",
    )
