from django.contrib import admin
from django.utils.translation import ngettext

from apps.locals.user_data import EntityModelAdmin

from ..models import UpgradePolicy


@admin.register(UpgradePolicy)
class UpgradePolicyAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "channel",
        "interval_display",
        "requires_canaries",
        "requires_pypi",
        "is_active",
    )
    search_fields = ("name", "description")
    list_filter = ("is_active", "channel", "requires_canaries", "requires_pypi_packages")

    actions = ("activate_selected_policies", "deactivate_selected_policies")

    @admin.display(description="Interval", ordering="interval_minutes")
    def interval_display(self, obj: UpgradePolicy) -> str:
        """Return the policy interval in a human-readable format."""

        interval_minutes = int(obj.interval_minutes or 0)
        if interval_minutes <= 0:
            return "-"

        minutes_per_day = 1440
        minutes_per_hour = 60

        if interval_minutes % minutes_per_day == 0:
            days = interval_minutes // minutes_per_day
            return ngettext("%(count)d day", "%(count)d days", days) % {"count": days}
        if interval_minutes % minutes_per_hour == 0:
            hours = interval_minutes // minutes_per_hour
            return ngettext("%(count)d hour", "%(count)d hours", hours) % {"count": hours}
        return ngettext("%(count)d minute", "%(count)d minutes", interval_minutes) % {
            "count": interval_minutes
        }

    @admin.display(boolean=True, description="Requires PyPI")
    def requires_pypi(self, obj: UpgradePolicy) -> bool:
        """Return whether the policy requires up-to-date PyPI packages."""

        return obj.requires_pypi_packages

    @admin.action(description="Activate selected upgrade policies")
    def activate_selected_policies(self, request, queryset):
        """Mark selected upgrade policies as active."""

        queryset.update(is_active=True)

    @admin.action(description="Deactivate selected upgrade policies")
    def deactivate_selected_policies(self, request, queryset):
        """Mark selected upgrade policies as inactive."""

        queryset.update(is_active=False)
