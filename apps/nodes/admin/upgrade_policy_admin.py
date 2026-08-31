from django.contrib import admin
from django.utils.translation import ngettext

from apps.locals.user_data import EntityModelAdmin

from ..models import UpgradePolicy


@admin.register(UpgradePolicy)
class UpgradePolicyAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "channel",
        "target_branch",
        "interval_display",
        "custom_allowed_bumps",
        "include_live_branch",
        "requires_pypi",
        "is_active",
    )
    search_fields = ("name", "description", "target_branch")
    list_filter = (
        "is_active",
        "channel",
        "include_live_branch",
        "requires_pypi_packages",
        "allow_patch_upgrades",
        "allow_minor_upgrades",
        "allow_major_upgrades",
    )

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

    @admin.display(description="Allowed bumps")
    def custom_allowed_bumps(self, obj: UpgradePolicy) -> str:
        """Return custom policy version bump gates."""

        if obj.channel != UpgradePolicy.Channel.CUSTOM:
            return "-"

        allowed = []
        if obj.allow_patch_upgrades:
            allowed.append("patch")
        if obj.allow_minor_upgrades:
            allowed.append("minor")
        if obj.allow_major_upgrades:
            allowed.append("major")
        return ", ".join(allowed) or "none"

    @admin.action(description="Activate selected upgrade policies")
    def activate_selected_policies(self, request, queryset):
        """Mark selected upgrade policies as active."""

        queryset.update(is_active=True)

    @admin.action(description="Deactivate selected upgrade policies")
    def deactivate_selected_policies(self, request, queryset):
        """Mark selected upgrade policies as inactive."""

        queryset.update(is_active=False)
