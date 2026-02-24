"""Availability-related charger admin actions."""

from django.contrib import admin, messages
from django.utils import timezone

from ....models import Charger
from .services import ActionServiceMixin


class AvailabilityActionsMixin(ActionServiceMixin):
    """Actions to request and set charger availability state."""

    def _dispatch_change_availability(self, request, queryset, availability_type: str):
        sent = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                payload = {"connectorId": charger.connector_id if charger.connector_id is not None else 0, "type": availability_type}
                if self._send_local_ocpp_call(request, charger, action="ChangeAvailability", payload=payload, pending_payload={"availability_type": availability_type}):
                    timestamp = timezone.now()
                    updates = {"availability_requested_state": availability_type, "availability_requested_at": timestamp, "availability_request_status": "", "availability_request_status_at": None, "availability_request_details": ""}
                    Charger.objects.filter(pk=charger.pk).update(**updates)
                    for field, value in updates.items():
                        setattr(charger, field, value)
                    sent += 1
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
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "change-availability", {"availability_type": availability_type})
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1
        if sent:
            self.message_user(request, f"Sent ChangeAvailability ({availability_type}) to {sent} charger(s)")

    @admin.action(description="Set availability to Operative")
    def change_availability_operative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Operative")

    @admin.action(description="Set availability to Inoperative")
    def change_availability_inoperative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Inoperative")

    def _set_availability_state(self, request, queryset, availability_state: str) -> None:
        updated = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                timestamp = timezone.now()
                updates = {"availability_state": availability_state, "availability_state_updated_at": timestamp}
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
                updated += 1
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
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "set-availability-state", {"availability_state": availability_state})
            if success:
                self._apply_remote_updates(charger, updates)
                updated += 1
        if updated:
            self.message_user(request, f"Updated availability to {availability_state} for {updated} charger(s)")

    @admin.action(description="Mark availability as Operative")
    def set_availability_state_operative(self, request, queryset):
        self._set_availability_state(request, queryset, "Operative")

    @admin.action(description="Mark availability as Inoperative")
    def set_availability_state_inoperative(self, request, queryset):
        self._set_availability_state(request, queryset, "Inoperative")
