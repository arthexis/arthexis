from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from ..models import UpgradePolicy


@admin.register(UpgradePolicy)
class UpgradePolicyAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "channel",
        "interval_minutes",
        "requires_canaries",
        "requires_pypi",
        "is_active",
    )
    search_fields = ("name", "description")
    list_filter = ("is_active", "channel", "requires_canaries", "requires_pypi_packages")

    actions = ("activate_selected_policies", "deactivate_selected_policies")

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
