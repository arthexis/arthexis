"""Remote control charger admin actions."""

from django.contrib import admin, messages

from .... import store
from .services import ActionServiceMixin


class RemoteControlActionsMixin(ActionServiceMixin):
    """Actions for unlock, remote stop and reset operations."""

    @admin.action(description="Unlock connector")
    def unlock_connector(self, request, queryset):
        unlocked = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            connector_value = charger.connector_id
            if connector_value in (None, 0):
                self.message_user(request, f"{charger}: connector id is required to send UnlockConnector.", level=messages.ERROR)
                continue
            if charger.is_local:
                payload = {"connectorId": connector_value}
                if self._send_local_ocpp_call(request, charger, action="UnlockConnector", payload=payload, pending_payload={}, timeout_kwargs={"message": "UnlockConnector request timed out"}):
                    unlocked += 1
                continue
            if not charger.allow_remote:
                self.message_user(request, f"{charger}: remote administration is disabled.", level=messages.ERROR)
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "unlock-connector")
            if success:
                self._apply_remote_updates(charger, updates)
                unlocked += 1
        if unlocked:
            self.message_user(request, f"Sent UnlockConnector to {unlocked} charger(s)")

    @admin.action(description="Remote stop active transaction")
    def remote_stop_transaction(self, request, queryset):
        stopped = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
                if tx_obj is None:
                    self.message_user(request, f"{charger}: no active transaction", level=messages.ERROR)
                    continue
                if self._send_local_ocpp_call(request, charger, action="RemoteStopTransaction", payload={"transactionId": tx_obj.pk}, pending_payload={"transaction_id": tx_obj.pk}):
                    stopped += 1
                continue
            if not charger.allow_remote:
                self.message_user(request, f"{charger}: remote administration is disabled.", level=messages.ERROR)
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "remote-stop")
            if success:
                self._apply_remote_updates(charger, updates)
                stopped += 1
        if stopped:
            self.message_user(request, f"Sent RemoteStopTransaction to {stopped} charger(s)")

    @admin.action(description="Reset charger (soft)")
    def reset_chargers(self, request, queryset):
        reset = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                if store.get_transaction(charger.charger_id, charger.connector_id) is not None:
                    self.message_user(request, f"{charger}: reset skipped because a session is active; stop the session first.", level=messages.WARNING)
                    continue
                if self._send_local_ocpp_call(request, charger, action="Reset", payload={"type": "Soft"}, pending_payload={}, timeout_kwargs={"timeout": 5.0, "message": "Reset timed out: charger did not respond"}):
                    reset += 1
                continue
            if not charger.allow_remote:
                self.message_user(request, f"{charger}: remote administration is disabled.", level=messages.ERROR)
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "reset", {"reset_type": "Soft"})
            if success:
                self._apply_remote_updates(charger, updates)
                reset += 1
        if reset:
            self.message_user(request, f"Sent Reset to {reset} charger(s)")
