"""Transaction and remote-operation admin actions for charge points."""

from __future__ import annotations


class TransactionsActionsMixin:
    """Delegate transaction and remote action behavior to the registered admin."""

    def _prepare_remote_credentials(self, request):
        return super()._prepare_remote_credentials(request)

    def _call_remote_action(self, request, local_node, private_key, charger, action: str, extra=None):
        return super()._call_remote_action(
            request,
            local_node,
            private_key,
            charger,
            action,
            extra=extra,
        )

    def _apply_remote_updates(self, charger, updates):
        return super()._apply_remote_updates(charger, updates)

    def recheck_charger_status(self, request, queryset):
        return super().recheck_charger_status(request, queryset)

    def fetch_cp_configuration(self, request, queryset):
        return super().fetch_cp_configuration(request, queryset)

    def clear_charging_profiles(self, request, queryset):
        return super().clear_charging_profiles(request, queryset)

    def unlock_connector(self, request, queryset):
        return super().unlock_connector(request, queryset)

    def remote_stop_transaction(self, request, queryset):
        return super().remote_stop_transaction(request, queryset)

    def reset_chargers(self, request, queryset):
        return super().reset_chargers(request, queryset)
