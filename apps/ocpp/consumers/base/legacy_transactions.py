"""Legacy transaction handlers split from the CSMS consumer class."""

import logging

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.cards.models import RFID as CoreRFID, RFIDAttempt
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import Transaction
from ...utils import _parse_ocpp_timestamp
from .identity import _extract_vehicle_identifier

logger = logging.getLogger(__name__)


class LegacyTransactionHandlersMixin:
    """Provide legacy OCPP 1.6/2.x transaction persistence flows."""

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "TransactionEvent")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "TransactionEvent")
    async def _handle_transaction_event_legacy(self, payload, _msg_id, _raw, text_data):
        """Persist OCPP 2.x transaction events using legacy transaction storage flow."""
        event_type = str(payload.get("eventType") or "").strip().lower()
        transaction_info = payload.get("transactionInfo") or {}
        ocpp_tx_id = str(transaction_info.get("transactionId") or "").strip()
        vid_value, vin_value = _extract_vehicle_identifier(payload)
        evse_info = payload.get("evse") or {}
        connector_hint = evse_info.get("connectorId", evse_info.get("id"))
        await self._assign_connector(connector_hint)
        connector_value = self.connector_value
        timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
        if timestamp_value is None:
            timestamp_value = timezone.now()

        def _record_transaction_event(tx_obj: Transaction | None, extra: dict[str, object] | None = None) -> None:
            notification: dict[str, object] = {
                "charger_id": getattr(self, "charger_id", None) or self.store_key,
                "connector_id": store.connector_slug(connector_value),
                "event_type": event_type,
                "timestamp": timestamp_value,
                "transaction_pk": getattr(tx_obj, "pk", None),
                "ocpp_transaction_id": ocpp_tx_id or getattr(tx_obj, "ocpp_transaction_id", None),
            }
            if transaction_info:
                if "meterStart" in transaction_info:
                    notification["meter_start"] = transaction_info.get("meterStart")
                if "meterStop" in transaction_info:
                    notification["meter_stop"] = transaction_info.get("meterStop")
            if extra:
                notification.update(extra)
            store.record_transaction_event(notification)

        id_token = payload.get("idToken") or {}
        id_tag = ""
        if isinstance(id_token, dict):
            id_tag = str(id_token.get("idToken") or "").strip()

        if event_type == "started":
            requests_to_start = store.find_transaction_requests(
                charger_id=self.charger_id,
                connector_id=connector_value,
                action="RequestStartTransaction",
                statuses={"accepted", "requested"},
            )
            tag = None
            tag_created = False
            if id_tag:
                tag, tag_created = await database_sync_to_async(CoreRFID.register_scan)(id_tag)
            account = await self._get_account(id_tag)
            if id_tag and not self.charger.require_rfid:
                seen_tag = await self._ensure_rfid_seen(id_tag, tag=tag)
                if seen_tag:
                    tag = seen_tag
            authorized = True
            authorized_via_tag = False
            if self.charger.require_rfid:
                if account is not None:
                    authorized = await database_sync_to_async(account.can_authorize)()
                elif id_tag and tag and not tag_created and getattr(tag, "allowed", False):
                    authorized = True
                    authorized_via_tag = True
                else:
                    authorized = False
            if authorized:
                update_kwargs: dict[str, str] = {"status": "started"}
                if ocpp_tx_id:
                    update_kwargs["transaction_id"] = ocpp_tx_id
                for request_message_id, _ in requests_to_start:
                    store.update_transaction_request(request_message_id, **update_kwargs)
                if authorized_via_tag and tag:
                    self._log_unlinked_rfid(tag.rfid)
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    charger=self.charger,
                    account=account,
                    rfid=(id_tag or ""),
                    vid=vid_value,
                    vin=vin_value,
                    connector_id=connector_value,
                    meter_start=transaction_info.get("meterStart"),
                    start_time=timestamp_value,
                    received_start_time=timezone.now(),
                    ocpp_transaction_id=ocpp_tx_id,
                )
                await self._ensure_ocpp_transaction_identifier(tx_obj, ocpp_tx_id)
                store.transactions[self.store_key] = tx_obj
                store.start_session_log(self.store_key, tx_obj.pk)
                store.start_session_lock()
                store.add_session_message(self.store_key, text_data)
                await self._start_consumption_updates(tx_obj)
                await self._process_meter_value_entries(payload.get("meterValue"), connector_value, tx_obj)
                _record_transaction_event(tx_obj)
                await self._record_rfid_attempt(
                    rfid=id_tag or "",
                    status=RFIDAttempt.Status.ACCEPTED,
                    account=account,
                    transaction=tx_obj,
                )
                return {"idTokenInfo": {"status": "Accepted"}}

            rejected_time = timezone.now()
            await database_sync_to_async(Transaction.objects.create)(
                charger=self.charger,
                account=account,
                rfid=(id_tag or ""),
                connector_id=connector_value,
                start_time=timestamp_value,
                received_start_time=rejected_time,
                ocpp_transaction_id=ocpp_tx_id,
                authorization_status=Transaction.AuthorizationStatus.REJECTED,
                authorization_reason="Invalid",
                rejected_at=rejected_time,
            )
            await self._record_rfid_attempt(
                rfid=id_tag or "", status=RFIDAttempt.Status.REJECTED, account=account
            )
            return {"idTokenInfo": {"status": "Invalid"}}

        if event_type == "ended":
            trigger_reason = str((payload.get("triggerReason") or "")).strip()
            tx_obj = store.transactions.pop(self.store_key, None)
            if not tx_obj and ocpp_tx_id:
                tx_obj = await Transaction.aget_by_ocpp_id(self.charger, ocpp_tx_id)
            if not tx_obj and ocpp_tx_id.isdigit():
                tx_obj = await database_sync_to_async(
                    Transaction.objects.filter(pk=int(ocpp_tx_id), charger=self.charger).first
                )()
            if tx_obj is None:
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    charger=self.charger,
                    connector_id=connector_value,
                    start_time=timestamp_value,
                    received_start_time=timestamp_value,
                    ocpp_transaction_id=ocpp_tx_id,
                )
            await self._ensure_ocpp_transaction_identifier(tx_obj, ocpp_tx_id)
            tx_obj.stop_time = timestamp_value
            tx_obj.received_stop_time = timezone.now()
            meter_stop_value = transaction_info.get("meterStop")
            if meter_stop_value is not None:
                tx_obj.meter_stop = meter_stop_value
            stop_reason_value = trigger_reason[:64]
            if stop_reason_value:
                tx_obj.stop_reason = stop_reason_value
            if vid_value:
                tx_obj.vid = vid_value
            if vin_value:
                tx_obj.vin = vin_value
            await database_sync_to_async(tx_obj.save)()
            await self._process_meter_value_entries(payload.get("meterValue"), connector_value, tx_obj)
            _record_transaction_event(tx_obj)
            await self._update_consumption_message(tx_obj.pk)
            await self._cancel_consumption_message()
            transaction_reference = ocpp_tx_id or tx_obj.ocpp_transaction_id or str(tx_obj.pk)
            store.mark_transaction_requests(
                charger_id=self.charger_id,
                connector_id=connector_value,
                transaction_id=transaction_reference,
                actions={"RequestStartTransaction"},
                statuses={"started", "accepted", "requested"},
                status="completed",
            )
            store.mark_transaction_requests(
                charger_id=self.charger_id,
                connector_id=connector_value,
                transaction_id=transaction_reference,
                actions={"RequestStopTransaction"},
                statuses={"accepted", "requested"},
                status="completed",
            )
            store.end_session_log(self.store_key)
            store.stop_session_lock()
            return {}

        if event_type == "updated":
            tx_obj = store.transactions.get(self.store_key)
            if not tx_obj and ocpp_tx_id:
                tx_obj = await Transaction.aget_by_ocpp_id(self.charger, ocpp_tx_id)
            if not tx_obj and ocpp_tx_id.isdigit():
                tx_obj = await database_sync_to_async(
                    Transaction.objects.filter(pk=int(ocpp_tx_id), charger=self.charger).first
                )()
            if tx_obj is None:
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    charger=self.charger,
                    connector_id=connector_value,
                    start_time=timestamp_value,
                    received_start_time=timezone.now(),
                    ocpp_transaction_id=ocpp_tx_id,
                )
                store.start_session_log(self.store_key, tx_obj.pk)
                store.add_session_message(self.store_key, text_data)
                store.transactions[self.store_key] = tx_obj
            await self._ensure_ocpp_transaction_identifier(tx_obj, ocpp_tx_id)
            await self._process_meter_value_entries(payload.get("meterValue"), connector_value, tx_obj)
            _record_transaction_event(tx_obj)
            return {}

        safe_payload = {k: v for k, v in payload.items() if k not in ("idToken", "idTag")}
        logger.warning(
            "Unhandled TransactionEvent eventType=%r for charger=%s connector=%s payload=%s",
            event_type,
            getattr(self, "charger_id", "unknown"),
            connector_value,
            safe_payload,
        )
        return {}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StartTransaction")
    async def _handle_start_transaction_legacy(self, payload, _msg_id, _raw, text_data):
        """Persist OCPP 1.6 StartTransaction using legacy storage flow."""
        id_tag = payload.get("idTag")
        tag = None
        tag_created = False
        if id_tag:
            tag, tag_created = await database_sync_to_async(CoreRFID.register_scan)(id_tag)
        account = await self._get_account(id_tag)
        if id_tag and not self.charger.require_rfid:
            seen_tag = await self._ensure_rfid_seen(id_tag, tag=tag)
            if seen_tag:
                tag = seen_tag
        await self._assign_connector(payload.get("connectorId"))
        authorized = True
        authorized_via_tag = False
        if self.charger.require_rfid:
            if account is not None:
                authorized = await database_sync_to_async(account.can_authorize)()
            elif id_tag and tag and not tag_created and getattr(tag, "allowed", False):
                authorized = True
                authorized_via_tag = True
            else:
                authorized = False
        if authorized:
            if authorized_via_tag and tag:
                self._log_unlinked_rfid(tag.rfid)
            start_timestamp = _parse_ocpp_timestamp(payload.get("timestamp"))
            received_start = timezone.now()
            vid_value, vin_value = _extract_vehicle_identifier(payload)
            tx_obj = await database_sync_to_async(Transaction.objects.create)(
                charger=self.charger,
                account=account,
                rfid=(id_tag or ""),
                vid=vid_value,
                vin=vin_value,
                connector_id=payload.get("connectorId"),
                meter_start=payload.get("meterStart"),
                start_time=start_timestamp or received_start,
                received_start_time=received_start,
            )
            await self._ensure_ocpp_transaction_identifier(tx_obj)
            store.transactions[self.store_key] = tx_obj
            store.start_session_log(self.store_key, tx_obj.pk)
            store.start_session_lock()
            store.add_session_message(self.store_key, text_data)
            await self._start_consumption_updates(tx_obj)
            await self._record_rfid_attempt(
                rfid=id_tag or "",
                status=RFIDAttempt.Status.ACCEPTED,
                account=account,
                transaction=tx_obj,
            )
            return {"transactionId": tx_obj.pk, "idTagInfo": {"status": "Accepted"}}
        rejected_time = timezone.now()
        await database_sync_to_async(Transaction.objects.create)(
            charger=self.charger,
            account=account,
            rfid=(id_tag or ""),
            connector_id=payload.get("connectorId"),
            start_time=rejected_time,
            received_start_time=rejected_time,
            authorization_status=Transaction.AuthorizationStatus.REJECTED,
            authorization_reason="Invalid",
            rejected_at=rejected_time,
        )
        await self._record_rfid_attempt(
            rfid=id_tag or "",
            status=RFIDAttempt.Status.REJECTED,
            account=account,
        )
        return {"idTagInfo": {"status": "Invalid"}}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StopTransaction")
    async def _handle_stop_transaction_legacy(self, payload, _msg_id, _raw, text_data):
        """Persist OCPP 1.6 StopTransaction using legacy storage flow."""
        tx_id = payload.get("transactionId")
        vid_value, vin_value = _extract_vehicle_identifier(payload)
        tx_obj = store.transactions.pop(self.store_key, None)
        if not tx_obj and tx_id is not None:
            tx_obj = await database_sync_to_async(
                Transaction.objects.filter(pk=tx_id, charger=self.charger).first
            )()
        if not tx_obj and tx_id is not None:
            received_start = timezone.now()
            tx_obj = await database_sync_to_async(Transaction.objects.create)(
                pk=tx_id,
                charger=self.charger,
                start_time=received_start,
                received_start_time=received_start,
                meter_start=payload.get("meterStart") or payload.get("meterStop"),
                vid=vid_value,
                vin=vin_value,
            )
        if tx_obj:
            await self._ensure_ocpp_transaction_identifier(tx_obj, str(tx_id))
            stop_timestamp = _parse_ocpp_timestamp(payload.get("timestamp"))
            received_stop = timezone.now()
            meter_stop_value = payload.get("meterStop")
            if meter_stop_value is not None:
                tx_obj.meter_stop = meter_stop_value
            stop_reason_value = str((payload.get("reason") or "")).strip()[:64]
            if stop_reason_value:
                tx_obj.stop_reason = stop_reason_value
            if vid_value:
                tx_obj.vid = vid_value
            if vin_value:
                tx_obj.vin = vin_value
            tx_obj.stop_time = stop_timestamp or received_stop
            tx_obj.received_stop_time = received_stop
            await database_sync_to_async(tx_obj.save)()
            await self._update_consumption_message(tx_obj.pk)
        await self._cancel_consumption_message()
        if text_data:
            store.add_session_message(self.store_key, text_data)
        store.end_session_log(self.store_key)
        store.stop_session_lock()
        return {"idTagInfo": {"status": "Accepted"}}
