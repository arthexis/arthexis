"""Query and aggregation helpers for charger admin list and actions."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class ChargerQuerysetMixin:
    """Delegate queryset-oriented helper behavior to the registered admin."""

    def _has_active_session(self, charger):
        return _RegisteredChargerAdmin._has_active_session(self, charger)

    def display_name_with_fallback(self, obj):
        return _RegisteredChargerAdmin.display_name_with_fallback(self, obj)

    def _charger_display_name(self, charger):
        return _RegisteredChargerAdmin._charger_display_name(self, charger)

    def local_indicator(self, obj):
        return _RegisteredChargerAdmin.local_indicator(self, obj)

    def location_name(self, obj):
        return _RegisteredChargerAdmin.location_name(self, obj)

    def _build_purge_summaries(self, queryset):
        return _RegisteredChargerAdmin._build_purge_summaries(self, queryset)

    def purge_data(self, request, queryset):
        return _RegisteredChargerAdmin.purge_data(self, request, queryset)

    def delete_queryset(self, request, queryset):
        return _RegisteredChargerAdmin.delete_queryset(self, request, queryset)

    def delete_view(self, request, object_id, extra_context=None):
        return _RegisteredChargerAdmin.delete_view(
            self,
            request,
            object_id,
            extra_context=extra_context,
        )

    def changelist_view(self, request, extra_context=None):
        return _RegisteredChargerAdmin.changelist_view(self, request, extra_context=extra_context)

    def _charger_quick_stats_context(self, queryset):
        return _RegisteredChargerAdmin._charger_quick_stats_context(self, queryset)

    def _today_range(self):
        return _RegisteredChargerAdmin._today_range(self)
