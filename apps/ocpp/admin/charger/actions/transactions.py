"""Transaction and remote-operation admin actions for charge points."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class TransactionsActionsMixin:
    """Delegate transaction and remote action behavior to the registered admin."""

    def _prepare_remote_credentials(self, request):
        return _RegisteredChargerAdmin._prepare_remote_credentials(self, request)

    def _call_remote_action(self, request, local_node, private_key, charger, action: str, extra=None):
        return _RegisteredChargerAdmin._call_remote_action(
            self,
            request,
            local_node,
            private_key,
            charger,
            action,
            extra=extra,
        )

    def _apply_remote_updates(self, charger, updates):
        return _RegisteredChargerAdmin._apply_remote_updates(self, charger, updates)

    def recheck_charger_status(self, request, queryset):
        return _RegisteredChargerAdmin.recheck_charger_status(self, request, queryset)

    def fetch_cp_configuration(self, request, queryset):
        return _RegisteredChargerAdmin.fetch_cp_configuration(self, request, queryset)

    def clear_charging_profiles(self, request, queryset):
        return _RegisteredChargerAdmin.clear_charging_profiles(self, request, queryset)

    def unlock_connector(self, request, queryset):
        return _RegisteredChargerAdmin.unlock_connector(self, request, queryset)

    def remote_stop_transaction(self, request, queryset):
        return _RegisteredChargerAdmin.remote_stop_transaction(self, request, queryset)

    def reset_chargers(self, request, queryset):
        return _RegisteredChargerAdmin.reset_chargers(self, request, queryset)
