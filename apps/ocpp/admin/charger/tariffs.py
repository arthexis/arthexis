"""Tariff and pricing helpers for charger admin dashboards."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class ChargerTariffMixin:
    """Delegate tariff and pricing helper behavior to the registered admin."""

    def total_kw_display(self, obj):
        return super().total_kw_display(obj)

    def today_kw(self, obj):
        return super().today_kw(obj)

    def _tariff_active_at(self, tariff, moment):
        return _RegisteredChargerAdmin._tariff_active_at(tariff, moment)

    def _build_tariff_cache(self, reference_time):
        return super()._build_tariff_cache(reference_time)

    def _select_tariff_price(self, cache, zone, contract_type, reference_time):
        return super()._select_tariff_price(cache, zone, contract_type, reference_time)
