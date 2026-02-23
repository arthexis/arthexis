"""Query and aggregation helpers for charger admin list and actions."""

from __future__ import annotations

from functools import wraps

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class ChargerQuerysetMixin:
    """Delegate queryset-oriented helper behavior to the registered admin."""

    def _has_active_session(self, charger):
        return super()._has_active_session(charger)

    def display_name_with_fallback(self, obj):
        return super().display_name_with_fallback(obj)

    def _charger_display_name(self, charger):
        return super()._charger_display_name(charger)

    def local_indicator(self, obj):
        return super().local_indicator(obj)

    def location_name(self, obj):
        return super().location_name(obj)

    def _build_purge_summaries(self, queryset):
        return super()._build_purge_summaries(queryset)

    @wraps(_RegisteredChargerAdmin.purge_data)
    def purge_data(self, request, queryset):
        return super().purge_data(request, queryset)

    def delete_queryset(self, request, queryset):
        return super().delete_queryset(request, queryset)

    def delete_view(self, request, object_id, extra_context=None):
        return super().delete_view(request, object_id, extra_context=extra_context)

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, extra_context=extra_context)

    def _charger_quick_stats_context(self, queryset):
        return super()._charger_quick_stats_context(queryset)

    def _today_range(self):
        return super()._today_range()
