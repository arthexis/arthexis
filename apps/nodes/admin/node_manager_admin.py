from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import NodeManager


@admin.register(NodeManager)
class NodeManagerAdmin(EntityModelAdmin):
    list_display = ("__str__", "provider", "is_enabled", "default_domain")
    list_filter = ("provider", "is_enabled")
    search_fields = (
        "default_domain",
        "user__username",
        "group__name",
    )
    fieldsets = (
        (_("Owner"), {"fields": ("user", "group")}),
        (
            _("Credentials"),
            {"fields": ("api_key", "api_secret", "customer_id")},
        ),
        (
            _("Configuration"),
            {
                "fields": (
                    "provider",
                    "default_domain",
                    "use_sandbox",
                    "is_enabled",
                )
            },
        ),
    )
