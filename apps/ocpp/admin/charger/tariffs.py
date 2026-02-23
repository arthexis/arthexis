"""Tariff and pricing helpers for charger admin dashboards."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class ChargerTariffMixin:
    """Delegate tariff and pricing helper behavior to the registered admin."""

    def total_kw_display(self, obj):
        return _RegisteredChargerAdmin.total_kw_display(self, obj)

    def today_kw(self, obj):
        return _RegisteredChargerAdmin.today_kw(self, obj)

    def _tariff_active_at(self, tariff, moment):
        return _RegisteredChargerAdmin._tariff_active_at(self, tariff, moment)

    def _build_tariff_cache(self, queryset):
        return _RegisteredChargerAdmin._build_tariff_cache(self, queryset)

    def _select_tariff_price(self, tariff_cache, location_id, moment):
        return _RegisteredChargerAdmin._select_tariff_price(self, tariff_cache, location_id, moment)
