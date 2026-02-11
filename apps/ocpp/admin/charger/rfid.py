"""RFID sync and local authorization actions for charger admin."""

from ..common_imports import *


class ChargerRFIDMixin:
    """Mixin for local auth list and RFID synchronization actions."""

    def _build_local_authorization_list(self) -> list[dict[str, object]]:
        """Return the payload for SendLocalList with released RFIDs."""

        entries: list[dict[str, object]] = []
        standard_status = "Accepted"  # OCPP 1.6 idTagInfo status value
        queryset = (
            CoreRFID.objects.filter(released=True).order_by("rfid").only("rfid")
        )
        for tag in queryset.iterator():
            entry: dict[str, object] = {"idTag": tag.rfid}
            entry["idTagInfo"] = {"status": standard_status}
            entries.append(entry)
        return entries

    @admin.action(description="Toggle RFID Authentication")
    def toggle_rfid_authentication(self, request, queryset):
        enabled = 0
        disabled = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            new_value = not charger.require_rfid
            if charger.is_local:
                Charger.objects.filter(pk=charger.pk).update(require_rfid=new_value)
                charger.require_rfid = new_value
                if new_value:
                    enabled += 1
                else:
                    disabled += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "toggle-rfid",
                {"enable": new_value},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                if charger.require_rfid:
                    enabled += 1
                else:
                    disabled += 1

        if enabled or disabled:
            changes = []
            if enabled:
                changes.append(f"enabled for {enabled} charger(s)")
            if disabled:
                changes.append(f"disabled for {disabled} charger(s)")
            summary = "; ".join(changes)
            self.message_user(
                request,
                f"Updated RFID authentication: {summary}",
            )

    @admin.action(description="Send Local RFIDs to CP")
    def send_rfid_list_to_evcs(self, request, queryset):
        authorization_list = self._build_local_authorization_list()
        update_type = "Full"
        sent = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            list_version = (charger.local_auth_list_version or 0) + 1
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {
                    "listVersion": list_version,
                    "updateType": update_type,
                    "localAuthorizationList": authorization_list,
                }
                msg = json.dumps([2, message_id, "SendLocalList", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send SendLocalList ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "SendLocalList",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "list_version": list_version,
                        "list_size": len(authorization_list),
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="SendLocalList",
                    log_key=log_key,
                    message="SendLocalList request timed out",
                )
                sent += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            extra = {
                "local_authorization_list": [entry.copy() for entry in authorization_list],
                "list_version": list_version,
                "update_type": update_type,
            }
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "send-local-rfid-list",
                extra,
            )
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1

        if sent:
            self.message_user(
                request,
                f"Sent SendLocalList to {sent} charger(s)",
            )

    @admin.action(description="Update RFIDs from EVCS")
    def update_rfids_from_evcs(self, request, queryset):
        requested = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload: dict[str, object] = {}
                msg = json.dumps([2, message_id, "GetLocalListVersion", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetLocalListVersion ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetLocalListVersion",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="GetLocalListVersion",
                    log_key=log_key,
                    message="GetLocalListVersion request timed out",
                )
                requested += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "get-local-list-version",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                requested += 1

        if requested:
            self.message_user(
                request,
                f"Requested GetLocalListVersion from {requested} charger(s)",
            )
