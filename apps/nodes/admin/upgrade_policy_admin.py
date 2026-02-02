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
        "requires_pypi_packages",
    )
    search_fields = ("name", "description")
    list_filter = ("channel", "requires_canaries", "requires_pypi_packages")
