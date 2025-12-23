from __future__ import annotations

import re

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import EnergyTariff


@admin.register(EnergyTariff)
class EnergyTariffAdmin(EntityModelAdmin):
    list_display = (
        "contract_type_short",
        "zone",
        "period",
        "unit",
        "year",
        "price_mxn",
    )
    list_filter = ("year", "zone", "contract_type", "period", "season", "unit")
    search_fields = (
        "contract_type",
        "zone",
        "period",
        "season",
    )

    def get_model_perms(self, request):
        return {}

    @admin.display(description=_("Contract type"), ordering="contract_type")
    def contract_type_short(self, obj):
        match = re.search(r"\(([^)]+)\)", obj.get_contract_type_display())
        return match.group(1) if match else obj.get_contract_type_display()
