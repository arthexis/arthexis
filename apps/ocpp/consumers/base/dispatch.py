import base64
import json

from ... import store
from ...call_error_handlers import dispatch_call_error
from ...call_result_handlers import dispatch_call_result
from ...models import Charger


class DispatchMixin:
    async def receive(self, text_data=None, bytes_data=None):
        raw = self._normalize_raw_message(text_data, bytes_data)
        if raw is None:
            return
        store.add_log(self.store_key, raw, log_type="charger")
        store.add_session_message(self.store_key, raw)
        msg = self._parse_message(raw)
        if msg is None:
            return
        message_type = msg[0]
        if message_type == 2:
            await self._handle_call_message(msg, raw, text_data)
        elif message_type == 3:
            msg_id = msg[1] if len(msg) > 1 else ""
            payload = msg[2] if len(msg) > 2 else {}
            await self._handle_call_result(msg_id, payload, raw)
        elif message_type == 4:
            msg_id = msg[1] if len(msg) > 1 else ""
            error_code = msg[2] if len(msg) > 2 else ""
            description = msg[3] if len(msg) > 3 else ""
            details = msg[4] if len(msg) > 4 else {}
            await self._handle_call_error(msg_id, error_code, description, details, raw)

    def _normalize_raw_message(self, text_data, bytes_data):
        raw = text_data
        if raw is None and bytes_data is not None:
            raw = base64.b64encode(bytes_data).decode("ascii")
        return raw

    def _parse_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(msg, dict):
            ocpp_payload = msg.get("ocpp")
            if isinstance(ocpp_payload, list) and ocpp_payload:
                self.forwarding_meta = msg.get("meta")
                return ocpp_payload
        if not isinstance(msg, list) or not msg:
            return None
        return msg

    async def _handle_call_message(self, msg, raw, text_data):
        msg_id, action = msg[1], msg[2]
        payload = msg[3] if len(msg) > 3 else {}
        connector_hint = payload.get("connectorId") if isinstance(payload, dict) else None
        self._log_triggered_follow_up(action, connector_hint)
        await self._assign_connector(payload.get("connectorId"))
        action_handlers = {
            "BootNotification": self._handle_boot_notification_action,
            "DataTransfer": self._handle_data_transfer_action,
            "Heartbeat": self._handle_heartbeat_action,
            "StatusNotification": self._handle_status_notification_action,
            "Authorize": self._handle_authorize_action,
            "MeterValues": self._handle_meter_values_action,
            "TransactionEvent": self._handle_transaction_event_action,
            "SecurityEventNotification": self._handle_security_event_notification_action,
            "NotifyChargingLimit": self._handle_notify_charging_limit_action,
            "ClearedChargingLimit": self._handle_cleared_charging_limit_action,
            "NotifyCustomerInformation": self._handle_notify_customer_information_action,
            "NotifyDisplayMessages": self._handle_notify_display_messages_action,
            "NotifyEVChargingNeeds": self._handle_notify_ev_charging_needs_action,
            "NotifyEVChargingSchedule": self._handle_notify_ev_charging_schedule_action,
            "NotifyEvent": self._handle_notify_event_action,
            "NotifyMonitoringReport": self._handle_notify_monitoring_report_action,
            "NotifyReport": self._handle_notify_report_action,
            "CostUpdated": self._handle_cost_updated_action,
            "PublishFirmwareStatusNotification": self._handle_publish_firmware_status_notification_action,
            "ReportChargingProfiles": self._handle_report_charging_profiles_action,
            "DiagnosticsStatusNotification": self._handle_diagnostics_status_notification_action,
            "LogStatusNotification": self._handle_log_status_notification_action,
            "StartTransaction": self._handle_start_transaction_action,
            "StopTransaction": self._handle_stop_transaction_action,
            "FirmwareStatusNotification": self._handle_firmware_status_notification_action,
            "ReservationStatusUpdate": self._handle_reservation_status_update_action,
            "Get15118EVCertificate": self._handle_get_15118_ev_certificate_action,
            "GetCertificateStatus": self._handle_get_certificate_status_action,
            "SignCertificate": self._handle_sign_certificate_action,
        }
        reply_payload = {}
        handler = action_handlers.get(action)
        if handler:
            reply_payload = await handler(payload, msg_id, raw, text_data)
        response = [3, msg_id, reply_payload]
        await self.send(json.dumps(response))
        store.add_log(
            self.store_key, f"< {json.dumps(response)}", log_type="charger"
        )
        await self._forward_charge_point_message(action, raw)

    def _log_triggered_follow_up(self, action: str, connector_hint):
        follow_up = store.consume_triggered_followup(
            self.charger_id, action, connector_hint
        )
        if not follow_up:
            return
        follow_up_log_key = follow_up.get("log_key") or self.store_key
        target_label = follow_up.get("target") or action
        connector_slug_value = follow_up.get("connector")
        suffix = ""
        if connector_slug_value and connector_slug_value != store.AGGREGATE_SLUG:
            connector_letter = Charger.connector_letter_from_slug(connector_slug_value)
            if connector_letter:
                suffix = f" (connector {connector_letter})"
            else:
                suffix = f" (connector {connector_slug_value})"
        store.add_log(
            follow_up_log_key,
            f"TriggerMessage follow-up received: {target_label}{suffix}",
            log_type="charger",
        )

    async def _handle_call_result(
        self, message_id: str, payload: dict | None, raw: str
    ) -> None:
        metadata = store.pop_pending_call(message_id)
        if not metadata:
            return
        metadata_charger = metadata.get("charger_id")
        if metadata_charger and self.charger_id:
            metadata_serial = Charger.normalize_serial(str(metadata_charger)).casefold()
            consumer_serial = Charger.normalize_serial(self.charger_id).casefold()
            if metadata_serial and consumer_serial and metadata_serial != consumer_serial:
                return
        action = metadata.get("action")
        log_key = metadata.get("log_key") or self.store_key
        payload_data = payload if isinstance(payload, dict) else {}
        handled = await dispatch_call_result(
            self,
            action,
            message_id,
            metadata,
            payload_data,
            log_key,
        )
        forward_reply = getattr(self, "_forward_charge_point_reply", None)
        if callable(forward_reply):
            await forward_reply(message_id, raw)
        if handled:
            return
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
        )

    async def _handle_call_error(
        self,
        message_id: str,
        error_code: str | None,
        description: str | None,
        details: dict | None,
        raw: str,
    ) -> None:
        metadata = store.pop_pending_call(message_id)
        if not metadata:
            return
        metadata_charger = metadata.get("charger_id")
        if metadata_charger and self.charger_id:
            metadata_serial = Charger.normalize_serial(str(metadata_charger)).casefold()
            consumer_serial = Charger.normalize_serial(self.charger_id).casefold()
            if metadata_serial and consumer_serial and metadata_serial != consumer_serial:
                return
        action = metadata.get("action")
        log_key = metadata.get("log_key") or self.store_key
        handled = await dispatch_call_error(
            self,
            action,
            message_id,
            metadata,
            error_code,
            description,
            details,
            log_key,
        )
        forward_reply = getattr(self, "_forward_charge_point_reply", None)
        if callable(forward_reply):
            await forward_reply(message_id, raw)
        if handled:
            return
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            success=False,
            error_code=error_code,
            error_description=description,
            error_details=details,
        )


__all__ = ["DispatchMixin"]
