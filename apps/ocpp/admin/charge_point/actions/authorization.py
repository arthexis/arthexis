"""Authorization and configuration charger admin actions."""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.cards.models import RFID as CoreRFID
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ....models import Charger
from .services import ActionServiceMixin


class AuthorizationActionsMixin(ActionServiceMixin):
    """Actions for RFID/local auth list workflows and related cache/config calls."""

    def _build_local_authorization_list(self) -> list[dict[str, object]]:
        """Return released RFID values mapped to OCPP SendLocalList entries."""
        return [
            {"idTag": tag.rfid, "idTagInfo": {"status": "Accepted"}}
            for tag in CoreRFID.objects.filter(released=True).order_by("rfid").only("rfid").iterator()
        ]

    @admin.action(description="Fetch CP configuration")
    def fetch_cp_configuration(self, request, queryset):
        fetched = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                if self._send_local_ocpp_call(
                    request,
                    charger,
                    action="GetConfiguration",
                    payload={},
                    pending_payload={},
                    timeout_kwargs={"timeout": 5.0, "message": "GetConfiguration timed out: charger did not respond (operation may not be supported)"},
                ):
                    fetched += 1
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
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "get-configuration")
            if success:
                self._apply_remote_updates(charger, updates)
                fetched += 1
        if fetched:
            self.message_user(request, f"Requested configuration from {fetched} charger(s)")

    @admin.action(description="Toggle RFID Authentication")
    def toggle_rfid_authentication(self, request, queryset):
        enabled = disabled = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            new_value = not charger.require_rfid
            if charger.is_local:
                Charger.objects.filter(pk=charger.pk).update(require_rfid=new_value)
                charger.require_rfid = new_value
                enabled += int(new_value)
                disabled += int(not new_value)
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
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "toggle-rfid", {"enable": new_value})
            if success:
                self._apply_remote_updates(charger, updates)
                enabled += int(charger.require_rfid)
                disabled += int(not charger.require_rfid)
        if enabled or disabled:
            parts = []
            if enabled:
                parts.append(f"enabled for {enabled} charger(s)")
            if disabled:
                parts.append(f"disabled for {disabled} charger(s)")
            self.message_user(request, f"Updated RFID authentication: {'; '.join(parts)}")

    @admin.action(description="Send Local RFIDs to CP")
    def send_rfid_list_to_evcs(self, request, queryset):
        authorization_list = self._build_local_authorization_list()
        sent = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            list_version = (charger.local_auth_list_version or 0) + 1
            if charger.is_local:
                payload = {"listVersion": list_version, "updateType": "Full", "localAuthorizationList": authorization_list}
                if self._send_local_ocpp_call(request, charger, action="SendLocalList", payload=payload, pending_payload={"list_version": list_version, "list_size": len(authorization_list)}, timeout_kwargs={"message": "SendLocalList request timed out"}):
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
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "send-local-rfid-list", {"local_authorization_list": [entry.copy() for entry in authorization_list], "list_version": list_version, "update_type": "Full"})
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1
        if sent:
            self.message_user(request, f"Sent SendLocalList to {sent} charger(s)")

    @admin.action(description="Update RFIDs from EVCS")
    def update_rfids_from_evcs(self, request, queryset):
        requested = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                if self._send_local_ocpp_call(request, charger, action="GetLocalListVersion", payload={}, pending_payload={}, timeout_kwargs={"message": "GetLocalListVersion request timed out"}):
                    requested += 1
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
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "get-local-list-version")
            if success:
                self._apply_remote_updates(charger, updates)
                requested += 1
        if requested:
            self.message_user(request, f"Requested GetLocalListVersion from {requested} charger(s)")

    @admin.action(description="Clear charger authorization cache")
    def clear_authorization_cache(self, request, queryset):
        cleared = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                if self._send_local_ocpp_call(request, charger, action="ClearCache", payload={}, pending_payload={}, timeout_kwargs={}):
                    cleared += 1
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
            success, _updates = self._call_remote_action(request, local_node, private_key, charger, "clear-cache")
            if success:
                cleared += 1
        if cleared:
            self.message_user(request, f"Sent ClearCache to {cleared} charger(s)")

    @protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ClearChargingProfile")
    @admin.action(description="Clear charging profiles")
    def clear_charging_profiles(self, request, queryset):
        cleared = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                if self._send_local_ocpp_call(request, charger, action="ClearChargingProfile", payload={}, pending_payload={}, timeout_kwargs={}, connector_value=0):
                    cleared += 1
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
            success, _updates = self._call_remote_action(request, local_node, private_key, charger, "clear-charging-profile")
            if success:
                cleared += 1
        if cleared:
            self.message_user(request, f"Sent ClearChargingProfile to {cleared} charger(s)")

    @admin.action(description=_("Clear all selected CP data"))
    def purge_data(self, request, queryset):
        for charger in queryset:
            charger.purge()
        self.message_user(request, _("Purged selected charge point data."))

