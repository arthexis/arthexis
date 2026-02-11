"""Remote action dispatch for charger admin."""

from ..common_imports import *


class ChargerRemoteActionsMixin:
    """Mixin with remote/OCPP command dispatch helpers and actions."""

    def _prepare_remote_credentials(self, request):
        local = Node.get_local()
        if not local or not local.uuid:
            self.message_user(
                request,
                "Local node is not registered; remote actions are unavailable.",
                level=messages.ERROR,
            )
            return None, None
        private_key = local.get_private_key()
        if private_key is None:
            self.message_user(
                request,
                "Local node private key is unavailable; remote actions are disabled.",
                level=messages.ERROR,
            )
            return None, None
        return local, private_key

    def _call_remote_action(
        self,
        request,
        local_node: Node,
        private_key,
        charger: Charger,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        if not charger.node_origin:
            self.message_user(
                request,
                f"{charger}: remote node information is missing.",
                level=messages.ERROR,
            )
            return False, {}
        origin = charger.node_origin
        if not origin.port:
            self.message_user(
                request,
                f"{charger}: remote node port is not configured.",
                level=messages.ERROR,
            )
            return False, {}

        if not origin.get_remote_host_candidates():
            self.message_user(
                request,
                f"{charger}: remote node connection details are incomplete.",
                level=messages.ERROR,
            )
            return False, {}

        payload: dict[str, Any] = {
            "requester": str(local_node.uuid),
            "requester_mac": local_node.mac_address,
            "requester_public_key": local_node.public_key,
            "charger_id": charger.charger_id,
            "connector_id": charger.connector_id,
            "action": action,
        }
        if extra:
            payload.update(extra)

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}
        try:
            signature = private_key.sign(
                payload_json.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            headers["X-Signature"] = base64.b64encode(signature).decode()
        except Exception:
            self.message_user(
                request,
                "Unable to sign remote action payload; remote action aborted.",
                level=messages.ERROR,
            )
            return False, {}

        url = next(
            origin.iter_remote_urls("/nodes/network/chargers/action/"),
            "",
        )
        if not url:
            self.message_user(
                request,
                f"{charger}: no reachable hosts were reported for the remote node.",
                level=messages.ERROR,
            )
            return False, {}
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except RequestException as exc:
            self.message_user(
                request,
                f"{charger}: failed to contact remote node ({exc}).",
                level=messages.ERROR,
            )
            return False, {}

        try:
            data = response.json()
        except ValueError:
            self.message_user(
                request,
                f"{charger}: invalid response from remote node.",
                level=messages.ERROR,
            )
            return False, {}

        if response.status_code != 200 or data.get("status") != "ok":
            detail = data.get("detail") if isinstance(data, dict) else None
            if not detail:
                detail = response.text or "Remote node rejected the request."
            self.message_user(
                request,
                f"{charger}: {detail}",
                level=messages.ERROR,
            )
            return False, {}

        updates = data.get("updates", {}) if isinstance(data, dict) else {}
        if not isinstance(updates, dict):
            updates = {}
        return True, updates

    def _apply_remote_updates(self, charger: Charger, updates: dict[str, Any]) -> None:
        if not updates:
            return

        applied: dict[str, Any] = {}
        for field, value in updates.items():
            if field in self._REMOTE_DATETIME_FIELDS and isinstance(value, str):
                parsed = parse_datetime(value)
                if parsed and timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                applied[field] = parsed
            else:
                applied[field] = value

        Charger.objects.filter(pk=charger.pk).update(**applied)
        for field, value in applied.items():
            setattr(charger, field, value)

    @admin.action(description="Re-check Charger Status")
    def recheck_charger_status(self, request, queryset):
        requested = 0
        for charger in queryset:
            connector_value = charger.connector_id
            ws = store.get_connection(charger.charger_id, connector_value)
            if ws is None:
                self.message_user(
                    request,
                    f"{charger}: no active connection",
                    level=messages.ERROR,
                )
                continue
            payload: dict[str, object] = {"requestedMessage": "StatusNotification"}
            trigger_connector: int | None = None
            if connector_value is not None:
                payload["connectorId"] = connector_value
                trigger_connector = connector_value
            message_id = uuid.uuid4().hex
            msg = json.dumps([2, message_id, "TriggerMessage", payload])
            try:
                async_to_sync(ws.send)(msg)
            except Exception as exc:  # pragma: no cover - network error
                self.message_user(
                    request,
                    f"{charger}: failed to send TriggerMessage ({exc})",
                    level=messages.ERROR,
                )
                continue
            log_key = store.identity_key(charger.charger_id, connector_value)
            store.add_log(log_key, f"< {msg}", log_type="charger")
            store.register_pending_call(
                message_id,
                {
                    "action": "TriggerMessage",
                    "charger_id": charger.charger_id,
                    "connector_id": connector_value,
                    "log_key": log_key,
                    "trigger_target": "StatusNotification",
                    "trigger_connector": trigger_connector,
                    "requested_at": timezone.now(),
                },
            )
            store.schedule_call_timeout(
                message_id,
                timeout=5.0,
                action="TriggerMessage",
                log_key=log_key,
                message="TriggerMessage StatusNotification timed out",
            )
            requested += 1
        if requested:
            self.message_user(
                request,
                f"Requested status update from {requested} charger(s)",
            )

    @admin.action(description="Fetch CP configuration")
    def fetch_cp_configuration(self, request, queryset):
        fetched = 0
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
                payload = {}
                msg = json.dumps([2, message_id, "GetConfiguration", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetConfiguration ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetConfiguration",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    timeout=5.0,
                    action="GetConfiguration",
                    log_key=log_key,
                    message=(
                        "GetConfiguration timed out: charger did not respond"
                        " (operation may not be supported)"
                    ),
                )
                fetched += 1
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
                "get-configuration",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                fetched += 1

        if fetched:
            self.message_user(
                request,
                f"Requested configuration from {fetched} charger(s)",
            )

    def _dispatch_change_availability(self, request, queryset, availability_type: str):
        sent = 0
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
                connector_id = connector_value if connector_value is not None else 0
                message_id = uuid.uuid4().hex
                payload = {"connectorId": connector_id, "type": availability_type}
                msg = json.dumps([2, message_id, "ChangeAvailability", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ChangeAvailability ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                timestamp = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ChangeAvailability",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "availability_type": availability_type,
                        "requested_at": timestamp,
                    },
                )
                updates = {
                    "availability_requested_state": availability_type,
                    "availability_requested_at": timestamp,
                    "availability_request_status": "",
                    "availability_request_status_at": None,
                    "availability_request_details": "",
                }
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
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
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "change-availability",
                {"availability_type": availability_type},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1

        if sent:
            self.message_user(
                request,
                f"Sent ChangeAvailability ({availability_type}) to {sent} charger(s)",
            )

    @admin.action(description="Set availability to Operative")
    def change_availability_operative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Operative")

    @admin.action(description="Set availability to Inoperative")
    def change_availability_inoperative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Inoperative")

    def _set_availability_state(
        self, request, queryset, availability_state: str
    ) -> None:
        updated = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                timestamp = timezone.now()
                updates = {
                    "availability_state": availability_state,
                    "availability_state_updated_at": timestamp,
                }
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
                updated += 1
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
                "set-availability-state",
                {"availability_state": availability_state},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                updated += 1

        if updated:
            self.message_user(
                request,
                f"Updated availability to {availability_state} for {updated} charger(s)",
            )

    @admin.action(description="Mark availability as Operative")
    def set_availability_state_operative(self, request, queryset):
        self._set_availability_state(request, queryset, "Operative")

    @admin.action(description="Mark availability as Inoperative")
    def set_availability_state_inoperative(self, request, queryset):
        self._set_availability_state(request, queryset, "Inoperative")

    @admin.action(description="Clear charger authorization cache")
    def clear_authorization_cache(self, request, queryset):
        cleared = 0
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
                msg = json.dumps([2, message_id, "ClearCache", {}])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ClearCache ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ClearCache",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="ClearCache",
                    log_key=log_key,
                )
                cleared += 1
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
            success, _updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "clear-cache",
            )
            if success:
                cleared += 1

        if cleared:
            self.message_user(
                request,
                f"Sent ClearCache to {cleared} charger(s)",
            )

    @protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ClearChargingProfile")
    @admin.action(description="Clear charging profiles")
    def clear_charging_profiles(self, request, queryset):
        cleared = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            connector_value = 0
            if charger.is_local:
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
                msg = json.dumps([2, message_id, "ClearChargingProfile", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ClearChargingProfile ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ClearChargingProfile",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="ClearChargingProfile",
                    log_key=log_key,
                )
                cleared += 1
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
            success, _updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "clear-charging-profile",
            )
            if success:
                cleared += 1

        if cleared:
            self.message_user(
                request,
                f"Sent ClearChargingProfile to {cleared} charger(s)",
            )

    @admin.action(description="Unlock connector")
    def unlock_connector(self, request, queryset):
        unlocked = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            connector_value = charger.connector_id
            if connector_value in (None, 0):
                self.message_user(
                    request,
                    f"{charger}: connector id is required to send UnlockConnector.",
                    level=messages.ERROR,
                )
                continue

            if charger.is_local:
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {"connectorId": connector_value}
                msg = json.dumps([2, message_id, "UnlockConnector", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send UnlockConnector ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "UnlockConnector",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="UnlockConnector",
                    log_key=log_key,
                    message="UnlockConnector request timed out",
                )
                unlocked += 1
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
                "unlock-connector",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                unlocked += 1

        if unlocked:
            self.message_user(
                request,
                f"Sent UnlockConnector to {unlocked} charger(s)",
            )

    @admin.action(description="Remote stop active transaction")
    def remote_stop_transaction(self, request, queryset):
        stopped = 0
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
                tx_obj = store.get_transaction(charger.charger_id, connector_value)
                if tx_obj is None:
                    self.message_user(
                        request,
                        f"{charger}: no active transaction",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {"transactionId": tx_obj.pk}
                msg = json.dumps([
                    2,
                    message_id,
                    "RemoteStopTransaction",
                    payload,
                ])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send RemoteStopTransaction ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "RemoteStopTransaction",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "transaction_id": tx_obj.pk,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                stopped += 1
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
                "remote-stop",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                stopped += 1

        if stopped:
            self.message_user(
                request,
                f"Sent RemoteStopTransaction to {stopped} charger(s)",
            )

    @admin.action(description="Reset charger (soft)")
    def reset_chargers(self, request, queryset):
        reset = 0
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
                tx_obj = store.get_transaction(charger.charger_id, connector_value)
                if tx_obj is not None:
                    self.message_user(
                        request,
                        (
                            f"{charger}: reset skipped because a session is active; "
                            "stop the session first."
                        ),
                        level=messages.WARNING,
                    )
                    continue
                message_id = uuid.uuid4().hex
                msg = json.dumps([
                    2,
                    message_id,
                    "Reset",
                    {"type": "Soft"},
                ])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send Reset ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "Reset",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    timeout=5.0,
                    action="Reset",
                    log_key=log_key,
                    message="Reset timed out: charger did not respond",
                )
                reset += 1
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
                "reset",
                {"reset_type": "Soft"},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                reset += 1

        if reset:
            self.message_user(
                request,
                f"Sent Reset to {reset} charger(s)",
            )
