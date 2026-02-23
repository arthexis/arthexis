"""General charger admin actions not tied to a specific domain."""

from django.contrib import admin

from .services import ActionServiceMixin


class GeneralActionsMixin(ActionServiceMixin):
    """General actions shared across admin workflows."""

    @admin.action(description="Re-check Charger Status")
    def recheck_charger_status(self, request, queryset):
        requested = 0
        for charger in queryset:
            payload: dict[str, object] = {"requestedMessage": "StatusNotification"}
            if charger.connector_id is not None:
                payload["connectorId"] = charger.connector_id
            if self._send_local_ocpp_call(
                request,
                charger,
                action="TriggerMessage",
                payload=payload,
                pending_payload={"trigger_target": "StatusNotification", "trigger_connector": charger.connector_id},
                timeout_kwargs={"timeout": 5.0, "message": "TriggerMessage StatusNotification timed out"},
            ):
                requested += 1
        if requested:
            self.message_user(request, f"Requested status update from {requested} charger(s)")
