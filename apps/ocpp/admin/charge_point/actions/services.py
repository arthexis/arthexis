"""Internal service helpers for charger admin actions."""

import base64
import json
import uuid
from collections.abc import Iterator
from typing import Any

import requests
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib import messages
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from requests import RequestException

from apps.nodes.models import Node

from .... import store
from ....models import Charger, ControlOperationEvent, Transaction


class ActionServiceMixin:
    """Shared helpers for local/remote charger admin action dispatch."""

    _REMOTE_DATETIME_FIELDS = {
        "availability_requested_at",
        "availability_request_status_at",
        "availability_state_updated_at",
        "diagnostics_requested_at",
        "diagnostics_last_downloaded_at",
    }
    _REMOTE_UPDATABLE_FIELDS = {
        "require_rfid",
        "local_auth_list_version",
        "availability_requested_state",
        "availability_requested_at",
        "availability_request_status",
        "availability_request_status_at",
        "availability_request_details",
        "availability_state",
        "availability_state_updated_at",
        "diagnostics_location",
        "diagnostics_requested_at",
        "diagnostics_last_downloaded_at",
    }
    _REDACTED_CONTROL_PAYLOAD_VALUE = "***redacted***"

    def _log_control_operation(
        self,
        request,
        *,
        charger: Charger,
        action: str,
        transport: str,
        status: str,
        detail: str = "",
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
        transaction_id: int | None = None,
    ) -> None:
        transaction = None
        if transaction_id:
            transaction = Transaction.objects.filter(pk=transaction_id).first()
        ControlOperationEvent.objects.create(
            charger=charger,
            transaction=transaction,
            actor=getattr(request, "user", None),
            action=action,
            transport=transport,
            status=status,
            detail=(detail or "")[:255],
            request_payload=self._sanitize_control_payload(request_payload) or {},
            response_payload=self._sanitize_control_payload(response_payload) or {},
        )

    def _sanitize_control_payload(self, payload: Any) -> Any:
        """Return a sanitized control-operation payload suitable for event logs."""
        if isinstance(payload, dict):
            sanitized: dict[str, Any] = {}
            for key, value in payload.items():
                if key.lower() == "idtag":
                    sanitized[key] = self._REDACTED_CONTROL_PAYLOAD_VALUE
                    continue
                sanitized[key] = self._sanitize_control_payload(value)
            return sanitized
        if isinstance(payload, list):
            return [self._sanitize_control_payload(item) for item in payload]
        return payload

    def _send_local_ocpp_call(
        self,
        request,
        charger: Charger,
        *,
        action: str,
        payload: dict[str, object],
        pending_payload: dict[str, object],
        timeout_kwargs: dict[str, object] | None = None,
        connector_value=None,
    ) -> bool:
        """Send an OCPP call to a local charger and register pending tracking."""
        if connector_value is None:
            connector_value = charger.connector_id
        ws = store.get_connection(charger.charger_id, connector_value)
        if ws is None:
            self.message_user(request, f"{charger}: no active connection", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.LOCAL, status=ControlOperationEvent.Status.FAILED, detail="No active websocket connection", request_payload=payload)
            return False
        message_id = uuid.uuid4().hex
        msg = json.dumps([2, message_id, action, payload])
        try:
            async_to_sync(ws.send)(msg)
        except Exception as exc:  # pragma: no cover - network error
            self.message_user(request, f"{charger}: failed to send {action} ({exc})", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.LOCAL, status=ControlOperationEvent.Status.FAILED, detail=str(exc), request_payload=payload)
            return False
        log_key = store.identity_key(charger.charger_id, connector_value)
        store.add_log(log_key, f"< {msg}", log_type="charger")
        tracking_payload = {
            "action": action,
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        }
        tracking_payload.update(pending_payload)
        store.register_pending_call(message_id, tracking_payload)
        if timeout_kwargs is not None:
            store.schedule_call_timeout(message_id, log_key=log_key, action=action, **timeout_kwargs)
        self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.LOCAL, status=ControlOperationEvent.Status.SENT, request_payload=payload, transaction_id=pending_payload.get("transaction_id"))
        return True

    def _prepare_remote_credentials(self, request):
        """Load signing credentials for remote node actions."""
        local = Node.get_local()
        if not local or not local.uuid:
            self.message_user(request, "Local node is not registered; remote actions are unavailable.", level=messages.ERROR)
            return None, None
        private_key = local.get_private_key()
        if private_key is None:
            self.message_user(request, "Local node private key is unavailable; remote actions are disabled.", level=messages.ERROR)
            return None, None
        return local, private_key

    def _iter_chargers(self, request, queryset) -> Iterator[tuple[Charger, bool, Node | None, Any]]:
        """Yield chargers with resolved local/remote dispatch context."""
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                yield charger, True, None, None
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
            yield charger, False, local_node, private_key

    def _call_remote_action(self, request, local_node: Node, private_key, charger: Charger, action: str, extra: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
        """Invoke a remote action on the charger's managing node."""
        if not charger.node_origin:
            self.message_user(request, f"{charger}: remote node information is missing.", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail="Remote node information is missing")
            return False, {}
        origin = charger.node_origin
        if not origin.port:
            self.message_user(request, f"{charger}: remote node port is not configured.", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail="Remote node port is not configured")
            return False, {}
        if not origin.get_remote_host_candidates():
            self.message_user(request, f"{charger}: remote node connection details are incomplete.", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail="Remote node connection details are incomplete")
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
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
            headers["X-Signature"] = base64.b64encode(signature).decode()
        except ValueError as exc:
            self.message_user(request, f"Unable to sign remote action payload; remote action aborted ({exc}).", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail=str(exc), request_payload=payload)
            return False, {}

        url = next(origin.iter_remote_urls("/nodes/network/chargers/action/"), "")
        if not url:
            self.message_user(request, f"{charger}: no reachable hosts were reported for the remote node.", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail="No reachable remote host", request_payload=payload)
            return False, {}
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except RequestException as exc:
            self.message_user(request, f"{charger}: failed to contact remote node ({exc}).", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail=str(exc), request_payload=payload)
            return False, {}

        try:
            data = response.json()
        except ValueError:
            self.message_user(request, f"{charger}: invalid response from remote node.", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail="Invalid JSON response", request_payload=payload)
            return False, {}
        if not isinstance(data, dict):
            self.message_user(request, f"{charger}: {response.text or 'Remote node rejected the request.'}", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail=response.text or "Remote node rejected the request", request_payload=payload)
            return False, {}
        if response.status_code != 200 or data.get("status") != "ok":
            detail = data.get("detail")
            self.message_user(request, f"{charger}: {detail or response.text or 'Remote node rejected the request.'}", level=messages.ERROR)
            self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.FAILED, detail=str(detail or response.text or "Remote node rejected the request"), request_payload=payload, response_payload=data if isinstance(data, dict) else {})
            return False, {}
        updates = data.get("updates", {})
        self._log_control_operation(request, charger=charger, action=action, transport=ControlOperationEvent.Transport.REMOTE, status=ControlOperationEvent.Status.SENT, request_payload=payload, response_payload=data if isinstance(data, dict) else {})
        return True, updates if isinstance(updates, dict) else {}

    def _apply_remote_updates(self, charger: Charger, updates: dict[str, Any]) -> None:
        """Persist updates returned by a remote action call."""
        if not updates:
            return
        applied: dict[str, Any] = {}
        for field, value in updates.items():
            if field not in self._REMOTE_UPDATABLE_FIELDS:
                continue
            if field in self._REMOTE_DATETIME_FIELDS and isinstance(value, str):
                parsed = parse_datetime(value)
                if parsed and timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                applied[field] = parsed
            else:
                applied[field] = value
        if not applied:
            return
        Charger.objects.filter(pk=charger.pk).update(**applied)
        for field, value in applied.items():
            setattr(charger, field, value)
