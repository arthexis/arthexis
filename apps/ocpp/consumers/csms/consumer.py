import asyncio
import inspect
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.cards.models import RFID as CoreRFID
from apps.cards.models import RFIDAttempt
from apps.energy.models import CustomerAccount
from apps.nodes.models import NetMessage
from apps.ocpp import store
from apps.ocpp.consumers.base.actions_metering import MeteringActionsMixin
from apps.ocpp.consumers.base.actions_notifications import NotificationActionsMixin
from apps.ocpp.consumers.base.actions_transactions import TransactionActionsMixin
from apps.ocpp.consumers.base.certificates import CertificatesMixin
from apps.ocpp.consumers.base.connection import ConnectionHandler
from apps.ocpp.consumers.base.connection_flow import ConnectionFlowMixin
from apps.ocpp.consumers.base.dispatch import DispatchMixin
from apps.ocpp.consumers.base.identity import (
    IdentityMixin,
    _extract_vehicle_identifier,
    _resolve_client_ip,
)
from apps.ocpp.consumers.base.legacy_transactions import LegacyTransactionHandlersMixin
from apps.ocpp.consumers.base.rfid import RfidMixin
from apps.ocpp.consumers.connection import (
    RateLimitedConnectionMixin,
    SubprotocolConnectionMixin,
    WebsocketAuthMixin,
)
from apps.ocpp.consumers.constants import (
    OCPP_CONNECT_RATE_LIMIT_FALLBACK,
    OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS,
    OCPP_VERSION_16,
    OCPP_VERSION_21,
    OCPP_VERSION_201,
)
from apps.ocpp.consumers.csms.actions import build_action_handlers
from apps.ocpp.consumers.csms.connectors import CSMSConnectorAssignmentMixin
from apps.ocpp.consumers.csms.handlers.availability import AvailabilityHandlersMixin
from apps.ocpp.consumers.csms.handlers.charging_profiles import (
    ChargingProfileHandlersMixin,
)
from apps.ocpp.consumers.csms.handlers.metering import MeteringHandlersMixin
from apps.ocpp.consumers.csms.handlers.notifications import (
    NotificationHandlersMixin as CsmsNotificationHandlersMixin,
)
from apps.ocpp.consumers.csms.handlers.status import StatusHandlersMixin
from apps.ocpp.consumers.csms.transport import CSMSTransportMixin
from apps.ocpp.consumers.path_metadata import bounded_last_path
from apps.ocpp.models import (
    Charger,
    ChargerConfiguration,
    ChargerLogRequest,
    ClearedChargingLimitEvent,
    CostUpdate,
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    CPReservation,
    CustomerInformationChunk,
    CustomerInformationRequest,
    DataTransferMessage,
    DeviceInventoryItem,
    DeviceInventorySnapshot,
    DisplayMessage,
    DisplayMessageNotification,
    MeterValue,
    MonitoringReport,
    MonitoringRule,
    PowerProjection,
    SecurityEvent,
    Transaction,
    Variable,
)
from apps.ocpp.status_resets import STATUS_RESET_UPDATES, clear_cached_statuses
from apps.ocpp.utils import _parse_ocpp_timestamp
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel
from config.offline import requires_network
from utils.rate_limit_fallback import fallback_rate_limit_allows

logger = logging.getLogger(__name__)

class RateLimitedConsumerMixin:
    """Local fallback limiter for OCPP websocket connections."""

    rate_limit_scope: str = "default"
    rate_limit_close_code: int = 4003
    rate_limit_fallback: int | None = None
    rate_limit_window: int = 60

    def get_rate_limit_identifier(self) -> str | None:
        client = getattr(self, "client_ip", None)
        if client:
            return client
        scope_client = getattr(self, "scope", {}).get("client")
        if isinstance(scope_client, (list, tuple)) and scope_client:
            return scope_client[0]
        return "unknown"

    async def enforce_rate_limit(self) -> bool:
        identifier = self.get_rate_limit_identifier()
        allowed = await sync_to_async(fallback_rate_limit_allows)(
            scope_key=self.rate_limit_scope,
            identifier=identifier,
            limit=self.rate_limit_fallback,
            window=self.rate_limit_window,
        )
        if not allowed:
            await self.handle_rate_limit_rejection(identifier)
            return False
        return True

    async def handle_rate_limit_rejection(self, identifier: str | None):
        await self.close(code=self.rate_limit_close_code)


class SinkConsumer(RateLimitedConsumerMixin, AsyncWebsocketConsumer):
    """Accept any message without validation."""

    rate_limit_scope = "sink-connect"
    rate_limit_fallback = store.MAX_CONNECTIONS_PER_IP
    rate_limit_window = 60

    @requires_network
    async def connect(self) -> None:
        self.client_ip = _resolve_client_ip(self.scope)
        if not await self.enforce_rate_limit():
            return
        await self.accept()

    async def disconnect(self, close_code):
        store.release_ip_connection(getattr(self, "client_ip", None), self)

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None
    ) -> None:
        if text_data is None:
            return
        try:
            msg = json.loads(text_data)
            if isinstance(msg, list) and msg and msg[0] == 2:
                await self.send(json.dumps([3, msg[1], {}]))
        except Exception:
            pass


class CSMSConsumer(
    CSMSTransportMixin,
    CSMSConnectorAssignmentMixin,
    AvailabilityHandlersMixin,
    ChargingProfileHandlersMixin,
    StatusHandlersMixin,
    MeteringHandlersMixin,
    CsmsNotificationHandlersMixin,
    ConnectionFlowMixin,
    RfidMixin,
    MeteringActionsMixin,
    TransactionActionsMixin,
    NotificationActionsMixin,
    LegacyTransactionHandlersMixin,
    IdentityMixin,
    CertificatesMixin,
    DispatchMixin,
    RateLimitedConnectionMixin,
    SubprotocolConnectionMixin,
    WebsocketAuthMixin,
    RateLimitedConsumerMixin,
    AsyncWebsocketConsumer,
):
    """Very small subset of OCPP 1.6 CSMS behaviour."""

    consumption_update_interval = 300
    rate_limit_target = Charger
    rate_limit_scope = "ocpp-connect"
    rate_limit_fallback = OCPP_CONNECT_RATE_LIMIT_FALLBACK
    rate_limit_window = OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS

    def get_rate_limit_identifier(self) -> str | None:
        if self._client_ip_is_local():
            return None
        return super().get_rate_limit_identifier()

    def _connection_handler(self) -> ConnectionHandler:
        """Return connection helper for OCPP 1.6/2.x admission checks."""

        return ConnectionHandler(self)

    def _action_handler(self, action: str):
        """Return a focused CSMS action handler."""

        handlers = getattr(self, "_focused_action_handlers", None)
        if handlers is None:
            handlers = build_action_handlers(self)
            self._focused_action_handlers = handlers
        return handlers[action]

    async def _ensure_ocpp_transaction_identifier(
        self, tx_obj: Transaction | None, ocpp_id: str | None = None
    ) -> None:
        """Persist a stable OCPP transaction identifier for lookups.

        The identifier is used to link OCPP 2.0.1 TransactionEvent messages to
        the stored :class:`~apps.ocpp.models.Transaction` even when the websocket
        session is rebuilt.
        """

        if not tx_obj:
            return
        normalized_id = (ocpp_id or "").strip()
        if normalized_id and tx_obj.ocpp_transaction_id != normalized_id:
            tx_obj.ocpp_transaction_id = normalized_id
            await database_sync_to_async(tx_obj.save)(
                update_fields=["ocpp_transaction_id"]
            )
            return
        if tx_obj.ocpp_transaction_id:
            return
        tx_obj.ocpp_transaction_id = str(tx_obj.pk)
        await database_sync_to_async(tx_obj.save)(update_fields=["ocpp_transaction_id"])

    async def _clear_cached_status_fields(self) -> None:
        """Reset cached status fields for the current charger identity."""
        await database_sync_to_async(clear_cached_statuses)([self.charger_id])
        for charger in (self.charger, self.aggregate_charger):
            if charger is None:
                continue
            for field, value in STATUS_RESET_UPDATES.items():
                setattr(charger, field, value)

    async def _store_meter_values(self, payload: dict, raw_message: str) -> None:
        """Parse a MeterValues payload into MeterValue rows."""
        connector_raw = payload.get("connectorId")
        connector_value = None
        if connector_raw is not None:
            try:
                connector_value = int(connector_raw)
            except (TypeError, ValueError):
                connector_value = None
        await self._assign_connector(connector_value)
        tx_id = payload.get("transactionId")
        tx_pk: int | None = None
        if tx_id is not None:
            try:
                tx_pk = int(tx_id)
            except (TypeError, ValueError):
                tx_pk = None
        tx_obj = store.transactions.get(self.store_key)
        if tx_id is not None:
            needs_lookup = False
            if tx_pk is not None:
                if not tx_obj or tx_obj.pk != tx_pk:
                    needs_lookup = True
            elif not tx_obj or tx_obj.ocpp_transaction_id != str(tx_id):
                needs_lookup = True

            if needs_lookup:
                if tx_pk is not None:
                    tx_obj = await database_sync_to_async(
                        Transaction.objects.filter(pk=tx_pk, charger=self.charger).first
                    )()
                else:
                    tx_obj = await Transaction.aget_by_ocpp_id(self.charger, str(tx_id))

            tx_id_value = str(tx_id).strip()
            if tx_obj is None and tx_id_value:
                create_kwargs = {
                    "charger": self.charger,
                    "ocpp_transaction_id": tx_id_value,
                    "start_time": timezone.now(),
                }
                if tx_pk is not None:
                    create_kwargs["pk"] = tx_pk
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    **create_kwargs
                )
                store.start_session_log(self.store_key, tx_obj.pk)
                store.add_session_message(self.store_key, raw_message)
            if tx_obj is not None:
                store.transactions[self.store_key] = tx_obj

        await self._ensure_ocpp_transaction_identifier(
            tx_obj, str(tx_id) if tx_id else None
        )
        await self._process_meter_value_entries(
            payload.get("meterValue"), connector_value, tx_obj
        )

    async def _process_meter_value_entries(
        self, meter_values: list[dict] | None, connector_value: int | None, tx_obj
    ) -> None:
        """Persist meter value samples and update transaction metrics."""

        readings = []
        updated_fields: set[str] = set()
        temperature = None
        temp_unit = ""
        for mv in meter_values or []:
            ts = parse_datetime(mv.get("timestamp"))
            values: dict[str, Decimal] = {}
            context = ""
            for sv in mv.get("sampledValue", []):
                try:
                    val = Decimal(str(sv.get("value")))
                except Exception:
                    continue
                context = sv.get("context", context or "")
                measurand = sv.get("measurand", "")
                unit = sv.get("unit", "")
                effective_unit = unit or self.charger.energy_unit
                field = None
                if measurand in ("", "Energy.Active.Import.Register"):
                    field = "energy"
                    val = self.charger.convert_energy_to_kwh(val, effective_unit)
                elif measurand == "Voltage":
                    field = "voltage"
                elif measurand == "Current.Import":
                    field = "current_import"
                elif measurand == "Current.Offered":
                    field = "current_offered"
                elif measurand == "Temperature":
                    field = "temperature"
                    temperature = val
                    temp_unit = unit
                elif measurand == "SoC":
                    field = "soc"
                if field:
                    if tx_obj and context in ("Transaction.Begin", "Transaction.End"):
                        suffix = "start" if context == "Transaction.Begin" else "stop"
                        if field == "energy":
                            meter_value_wh = int(val * Decimal("1000"))
                            setattr(tx_obj, f"meter_{suffix}", meter_value_wh)
                            updated_fields.add(f"meter_{suffix}")
                        else:
                            setattr(tx_obj, f"{field}_{suffix}", val)
                            updated_fields.add(f"{field}_{suffix}")
                    else:
                        values[field] = val
                        if tx_obj and field == "energy" and tx_obj.meter_start is None:
                            try:
                                tx_obj.meter_start = int(val * Decimal("1000"))
                            except (TypeError, ValueError):
                                pass
                            else:
                                updated_fields.add("meter_start")
            if values and context not in ("Transaction.Begin", "Transaction.End"):
                readings.append(
                    MeterValue(
                        charger=self.charger,
                        connector_id=connector_value,
                        transaction=tx_obj,
                        timestamp=ts,
                        context=context,
                        **values,
                    )
                )
        if readings:
            await database_sync_to_async(MeterValue.objects.bulk_create)(readings)
        if tx_obj and updated_fields:
            await database_sync_to_async(tx_obj.save)(
                update_fields=list(updated_fields)
            )
        if connector_value is not None and not self.charger.connector_id:
            self.charger.connector_id = connector_value
            await database_sync_to_async(self.charger.save)(
                update_fields=["connector_id"]
            )
        if temperature is not None:
            self.charger.temperature = temperature
            self.charger.temperature_unit = temp_unit
            await database_sync_to_async(self.charger.save)(
                update_fields=["temperature", "temperature_unit"]
            )

    async def _update_firmware_state(
        self, status: str, status_info: str, timestamp: datetime | None
    ) -> None:
        """Persist firmware status fields for the active charger identities."""

        targets: list[Charger] = []
        seen_ids: set[int] = set()
        for charger in (self.charger, self.aggregate_charger):
            if not charger or charger.pk is None:
                continue
            if charger.pk in seen_ids:
                continue
            targets.append(charger)
            seen_ids.add(charger.pk)

        if not targets:
            return

        def _persist(ids: list[int]) -> None:
            Charger.objects.filter(pk__in=ids).update(
                firmware_status=status,
                firmware_status_info=status_info,
                firmware_timestamp=timestamp,
            )

        await database_sync_to_async(_persist)([target.pk for target in targets])
        for target in targets:
            target.firmware_status = status
            target.firmware_status_info = status_info
            target.firmware_timestamp = timestamp

        def _update_deployments(ids: list[int]) -> None:
            deployments = list(
                CPFirmwareDeployment.objects.filter(
                    charger_id__in=ids, completed_at__isnull=True
                )
            )
            payload = {"status": status, "statusInfo": status_info}
            for deployment in deployments:
                deployment.mark_status(
                    status,
                    status_info,
                    timestamp,
                    response=payload,
                )

        await database_sync_to_async(_update_deployments)(
            [target.pk for target in targets]
        )

    async def _cancel_consumption_message(self) -> None:
        """Stop any scheduled consumption message updates."""

        task = self._consumption_task
        message_uuid = self._consumption_message_uuid
        self._consumption_task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if message_uuid:

            def _expire() -> None:
                msg = NetMessage.objects.filter(uuid=message_uuid).first()
                if not msg:
                    return
                msg.expires_at = timezone.now() + timedelta(seconds=1)
                msg.save(update_fields=["expires_at"])
                msg.propagate()

            await database_sync_to_async(_expire)()
        self._consumption_message_uuid = None

    async def _update_consumption_message(self, tx_id: int) -> str | None:
        """Create or update the Net Message for an active transaction."""

        existing_uuid = self._consumption_message_uuid

        def _subject_initials(value: str) -> str:
            characters = re.findall(r"\b(\w)", value)
            return "".join(characters).upper()

        def _format_elapsed(start: datetime | None) -> str:
            if not start:
                return "00:00:00"
            now_local = timezone.localtime(timezone.now())
            start_local = timezone.localtime(start)
            elapsed_seconds = max(0, int((now_local - start_local).total_seconds()))
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        def _persist() -> str | None:
            tx = Transaction.objects.select_related("charger").filter(pk=tx_id).first()
            if not tx:
                return None
            charger = tx.charger or self.charger
            subject_label = ""
            if charger:
                display_value = (
                    charger.display_name
                    or getattr(charger, "name", "")
                    or charger.charger_id
                    or ""
                )
                subject_label = _subject_initials(display_value)
            connector_value = tx.connector_id or getattr(charger, "connector_id", None)
            subject_suffix = f" CP{connector_value}" if connector_value else ""
            if not subject_label:
                subject_label = (
                    getattr(charger, "charger_id", "") or self.charger_id or ""
                )
            subject_value = f"{subject_label}{subject_suffix}".strip()[:64]
            if not subject_value:
                return None

            energy_consumed = tx.kw
            unit = getattr(charger, "energy_unit", Charger.EnergyUnit.KW)
            if unit == Charger.EnergyUnit.W:
                energy_consumed *= 1000
            elapsed_label = _format_elapsed(tx.start_time)
            body_value = f"{energy_consumed:.1f}{unit} {elapsed_label}"[:256]
            expires_at = timezone.now() + timedelta(
                seconds=max(self.consumption_update_interval * 2, 30)
            )
            if existing_uuid:
                msg = NetMessage.objects.filter(uuid=existing_uuid).first()
                if msg:
                    msg.subject = subject_value
                    msg.body = body_value
                    msg.expires_at = expires_at
                    msg.save(
                        update_fields=[
                            "subject",
                            "body",
                            "expires_at",
                        ]
                    )
                    msg.propagate()
                    return str(msg.uuid)
            msg = NetMessage.broadcast(
                subject=subject_value,
                body=body_value,
                expires_at=expires_at,
            )
            return str(msg.uuid)

        try:
            result = await database_sync_to_async(_persist)()
        except Exception as exc:  # pragma: no cover - unexpected errors
            store.add_log(
                self.store_key,
                f"Failed to broadcast consumption message: {exc}",
                log_type="charger",
            )
            return None
        if result is None:
            store.add_log(
                self.store_key,
                "Unable to broadcast consumption message: missing data",
                log_type="charger",
            )
            return None
        self._consumption_message_uuid = result
        return result

    async def _consumption_message_loop(self, tx_id: int) -> None:
        """Periodically refresh the consumption Net Message."""

        try:
            while True:
                await asyncio.sleep(self.consumption_update_interval)
                updated = await self._update_consumption_message(tx_id)
                if not updated:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - unexpected errors
            store.add_log(
                self.store_key,
                f"Failed to refresh consumption message: {exc}",
                log_type="charger",
            )

    async def _start_consumption_updates(self, tx_obj: Transaction) -> None:
        """Send the initial consumption message and schedule updates."""

        await self._cancel_consumption_message()
        initial = await self._update_consumption_message(tx_obj.pk)
        if not initial:
            return
        task = asyncio.create_task(self._consumption_message_loop(tx_obj.pk))
        task.add_done_callback(lambda _: setattr(self, "_consumption_task", None))
        self._consumption_task = task

    def _persist_configuration_result(
        self, payload: dict, connector_hint: int | str | None
    ) -> ChargerConfiguration | None:
        if not isinstance(payload, dict):
            return None

        connector_value: int | None = None
        if connector_hint not in (None, ""):
            try:
                connector_value = int(connector_hint)
            except (TypeError, ValueError):
                connector_value = None

        normalized_entries: list[dict[str, object]] = []
        for entry in payload.get("configurationKey") or []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            normalized: dict[str, object] = {"key": key}
            if "value" in entry:
                normalized["value"] = entry.get("value")
            normalized["readonly"] = bool(entry.get("readonly"))
            normalized_entries.append(normalized)

        unknown_values: list[str] = []
        for value in payload.get("unknownKey") or []:
            if value is None:
                continue
            unknown_values.append(str(value))

        try:
            raw_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            raw_payload = payload

        queryset = ChargerConfiguration.objects.filter(
            charger_identifier=self.charger_id
        )
        if connector_value is None:
            queryset = queryset.filter(connector_id__isnull=True)
        else:
            queryset = queryset.filter(connector_id=connector_value)

        existing = queryset.order_by("-created_at").first()
        if existing and existing.unknown_keys == unknown_values:
            if (
                existing.configuration_keys == normalized_entries
                and existing.raw_payload == raw_payload
            ):
                now = timezone.now()
                ChargerConfiguration.objects.filter(pk=existing.pk).update(
                    updated_at=now
                )
                existing.updated_at = now
                Charger.objects.filter(charger_id=self.charger_id).update(
                    configuration=existing
                )
                return existing

        configuration = ChargerConfiguration.objects.create(
            charger_identifier=self.charger_id,
            connector_id=connector_value,
            unknown_keys=unknown_values,
            evcs_snapshot_at=timezone.now(),
            raw_payload=raw_payload,
        )
        configuration.replace_configuration_keys(normalized_entries)
        Charger.objects.filter(charger_id=self.charger_id).update(
            configuration=configuration
        )
        return configuration

    def _apply_change_configuration_snapshot(
        self,
        key: str,
        value: str | None,
        connector_hint: int | str | None,
    ) -> ChargerConfiguration:
        connector_value: int | None = None
        if connector_hint not in (None, ""):
            try:
                connector_value = int(connector_hint)
            except (TypeError, ValueError):
                connector_value = None

        queryset = ChargerConfiguration.objects.filter(
            charger_identifier=self.charger_id
        )
        if connector_value is None:
            queryset = queryset.filter(connector_id__isnull=True)
        else:
            queryset = queryset.filter(connector_id=connector_value)

        configuration = queryset.order_by("-created_at").first()
        if configuration is None:
            configuration = ChargerConfiguration.objects.create(
                charger_identifier=self.charger_id,
                connector_id=connector_value,
                unknown_keys=[],
                evcs_snapshot_at=timezone.now(),
                raw_payload={},
            )

        entries = configuration.configuration_keys
        updated = False
        for entry in entries:
            if entry.get("key") == key:
                updated = True
                if value is None:
                    entry.pop("value", None)
                else:
                    entry["value"] = value
        if not updated:
            new_entry: dict[str, object] = {"key": key, "readonly": False}
            if value is not None:
                new_entry["value"] = value
            entries.append(new_entry)

        configuration.replace_configuration_keys(entries)

        raw_payload = configuration.raw_payload or {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        else:
            raw_payload = dict(raw_payload)

        payload_entries: list[dict[str, object]] = []
        seen = False
        for item in raw_payload.get("configurationKey", []):
            if not isinstance(item, dict):
                continue
            entry_copy = dict(item)
            if str(entry_copy.get("key") or "") == key:
                if value is None:
                    entry_copy.pop("value", None)
                else:
                    entry_copy["value"] = value
                seen = True
            payload_entries.append(entry_copy)
        if not seen:
            payload_entry: dict[str, object] = {"key": key}
            if value is not None:
                payload_entry["value"] = value
            payload_entries.append(payload_entry)

        raw_payload["configurationKey"] = payload_entries
        configuration.raw_payload = raw_payload
        configuration.evcs_snapshot_at = timezone.now()
        configuration.save(
            update_fields=["raw_payload", "evcs_snapshot_at", "updated_at"]
        )
        Charger.objects.filter(charger_id=self.charger_id).update(
            configuration=configuration
        )
        return configuration

    async def _handle_data_transfer(
        self, message_id: str, payload: dict | None
    ) -> dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        vendor_id = str(payload.get("vendorId") or "").strip()
        vendor_message_id = payload.get("messageId")
        if vendor_message_id is None:
            vendor_message_id_text = ""
        elif isinstance(vendor_message_id, str):
            vendor_message_id_text = vendor_message_id.strip()
        else:
            vendor_message_id_text = str(vendor_message_id)
        connector_value = self.connector_value

        def _get_or_create_charger():
            if self.charger and getattr(self.charger, "pk", None):
                return self.charger
            if connector_value is None:
                charger, _ = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=None,
                    defaults={"last_path": bounded_last_path(self.scope, Charger)},
                )
                return charger
            charger, _ = Charger.objects.get_or_create(
                charger_id=self.charger_id,
                connector_id=connector_value,
                defaults={"last_path": bounded_last_path(self.scope, Charger)},
            )
            return charger

        charger_obj = await database_sync_to_async(_get_or_create_charger)()
        message = await database_sync_to_async(DataTransferMessage.objects.create)(
            charger=charger_obj,
            connector_id=connector_value,
            direction=DataTransferMessage.DIRECTION_CP_TO_CSMS,
            ocpp_message_id=message_id,
            vendor_id=vendor_id,
            message_id=vendor_message_id_text,
            payload=payload or {},
            status="Pending",
        )

        status = "Rejected" if not vendor_id else "UnknownVendorId"
        response_data = None
        error_code = ""
        error_description = ""
        error_details = None

        handler = self._resolve_data_transfer_handler(vendor_id) if vendor_id else None
        if handler:
            try:
                result = handler(message, payload)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:  # pragma: no cover - defensive guard
                status = "Rejected"
                error_code = "InternalError"
                error_description = str(exc)
            else:
                if isinstance(result, tuple):
                    status = str(result[0]) if result else status
                    if len(result) > 1:
                        response_data = result[1]
                elif isinstance(result, dict):
                    status = str(result.get("status", status))
                    if "data" in result:
                        response_data = result["data"]
                elif isinstance(result, str):
                    status = result
        final_status = status or "Rejected"

        def _finalise():
            DataTransferMessage.objects.filter(pk=message.pk).update(
                status=final_status,
                response_data=response_data,
                error_code=error_code,
                error_description=error_description,
                error_details=error_details,
                responded_at=timezone.now(),
            )

        await database_sync_to_async(_finalise)()

        reply_payload: dict[str, object] = {"status": final_status}
        if response_data is not None:
            reply_payload["data"] = response_data
        return reply_payload

    def _resolve_data_transfer_handler(self, vendor_id: str):
        if not vendor_id:
            return None
        candidate = f"handle_data_transfer_{vendor_id.lower()}"
        return getattr(self, candidate, None)

    async def _update_change_availability_state(
        self,
        connector_value: int | None,
        requested_type: str | None,
        status: str,
        requested_at,
        *,
        details: str = "",
    ) -> None:
        status_value = status or ""
        now = timezone.now()

        def _apply():
            charger_identifier = getattr(self, "charger_id", None) or ""
            if not charger_identifier and getattr(self, "charger", None):
                charger_identifier = getattr(self.charger, "charger_id", None) or ""
            if not charger_identifier and getattr(self, "aggregate_charger", None):
                charger_identifier = (
                    getattr(self.aggregate_charger, "charger_id", None) or ""
                )
            if not charger_identifier:
                return
            charger_identifier = str(charger_identifier)

            filters: dict[str, object] = {"charger_id": charger_identifier}
            if connector_value is None:
                filters["connector_id__isnull"] = True
            else:
                filters["connector_id"] = connector_value
            targets = list(Charger.objects.filter(**filters))
            if not targets:
                return
            for target in targets:
                updates: dict[str, object] = {
                    "availability_request_status": status_value,
                    "availability_request_status_at": now,
                    "availability_request_details": details,
                }
                if requested_type:
                    updates["availability_requested_state"] = requested_type
                if requested_at:
                    updates["availability_requested_at"] = requested_at
                elif requested_type:
                    updates["availability_requested_at"] = now
                if status_value == "Accepted" and requested_type:
                    updates["availability_state"] = requested_type
                    updates["availability_state_updated_at"] = now
                Charger.objects.filter(pk=target.pk).update(**updates)
                for field, value in updates.items():
                    setattr(target, field, value)
                if self.charger and self.charger.pk == target.pk:
                    for field, value in updates.items():
                        setattr(self.charger, field, value)
                if self.aggregate_charger and self.aggregate_charger.pk == target.pk:
                    for field, value in updates.items():
                        setattr(self.aggregate_charger, field, value)

        await database_sync_to_async(_apply)()

    async def _update_local_authorization_state(self, version: int | None) -> None:
        """Persist the reported local authorization list version."""

        timestamp = timezone.now()

        def _apply() -> None:
            updates: dict[str, object] = {"local_auth_list_updated_at": timestamp}
            if version is not None:
                updates["local_auth_list_version"] = int(version)

            targets: list[Charger] = []
            if self.charger and getattr(self.charger, "pk", None):
                targets.append(self.charger)
            aggregate = self.aggregate_charger
            if (
                aggregate
                and getattr(aggregate, "pk", None)
                and not any(
                    target.pk == aggregate.pk for target in targets if target.pk
                )
            ):
                targets.append(aggregate)

            if not targets:
                return

            for target in targets:
                Charger.objects.filter(pk=target.pk).update(**updates)
                for field, value in updates.items():
                    setattr(target, field, value)

        await database_sync_to_async(_apply)()

    async def _apply_local_authorization_entries(
        self, entries: list[dict[str, object]]
    ) -> int:
        """Create or update RFID records from a local authorization list."""

        def _apply() -> int:
            processed = 0
            now = timezone.now()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                id_tag = entry.get("idTag")
                id_tag_text = str(id_tag or "").strip().upper()
                if not id_tag_text:
                    continue
                info = entry.get("idTagInfo")
                status_value = ""
                if isinstance(info, dict):
                    status_value = str(info.get("status") or "").strip()
                status_key = status_value.lower()
                allowed_flag = status_key in {"", "accepted", "concurrenttx"}
                defaults = {"allowed": allowed_flag, "released": allowed_flag}
                tag, _ = CoreRFID.update_or_create_from_code(id_tag_text, defaults)
                updates: set[str] = set()
                if tag.allowed != allowed_flag:
                    tag.allowed = allowed_flag
                    updates.add("allowed")
                if tag.released != allowed_flag:
                    tag.released = allowed_flag
                    updates.add("released")
                if tag.last_seen_on != now:
                    tag.last_seen_on = now
                    updates.add("last_seen_on")
                if updates:
                    tag.save(update_fields=sorted(updates))
                processed += 1
            return processed

        return await database_sync_to_async(_apply)()

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "BootNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "BootNotification")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "BootNotification")
    async def _handle_boot_notification_action(self, payload, msg_id, raw, text_data):
        payload_data = payload if isinstance(payload, dict) else {}
        normalized_payload = payload_data
        ocpp_version = str(getattr(self, "ocpp_version", "") or "")
        if ocpp_version.startswith("ocpp2."):
            normalized_payload = dict(payload_data)
            charging_station = payload_data.get("chargingStation")
            if isinstance(charging_station, dict):
                charge_point_vendor = str(
                    charging_station.get("vendorName") or ""
                ).strip()
                charge_point_model = str(charging_station.get("model") or "").strip()
                if (
                    charge_point_vendor
                    and "chargePointVendor" not in normalized_payload
                ):
                    normalized_payload["chargePointVendor"] = charge_point_vendor
                if charge_point_model and "chargePointModel" not in normalized_payload:
                    normalized_payload["chargePointModel"] = charge_point_model
            boot_reason = str(payload_data.get("reason") or "").strip()
            if boot_reason and "bootReason" not in normalized_payload:
                normalized_payload["bootReason"] = boot_reason
        logger.debug(
            "BootNotification payload normalized for %s.",
            ocpp_version or "unknown",
            extra={"payload": normalized_payload},
        )
        current_time = datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "currentTime": current_time,
            "interval": 300,
            "status": "Accepted",
        }

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "DataTransfer")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "DataTransfer")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "DataTransfer")
    async def _handle_data_transfer_action(self, payload, msg_id, raw, text_data):
        return await self._handle_data_transfer(msg_id, payload)

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Authorize")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Authorize")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "Authorize")
    async def _handle_authorize_action(self, payload, msg_id, raw, text_data):
        payload_data = payload if isinstance(payload, dict) else {}
        normalized_payload = payload_data
        if "idTag" not in payload_data:
            id_token = payload_data.get("idToken")
            if isinstance(id_token, dict):
                token_value = str(id_token.get("idToken") or "").strip()
                if token_value:
                    normalized_payload = {**payload_data, "idTag": token_value}

        response = await self._action_handler("Authorize").handle(
            normalized_payload, msg_id, raw, text_data
        )
        if not isinstance(response, dict):
            return response

        ocpp_version = str(getattr(self, "ocpp_version", "") or "")
        if not ocpp_version.startswith("ocpp2."):
            return response

        if isinstance(response.get("idTokenInfo"), dict):
            return response
        id_tag_info = response.get("idTagInfo")
        if not isinstance(id_tag_info, dict):
            return response
        return {key: value for key, value in response.items() if key != "idTagInfo"} | {
            "idTokenInfo": id_tag_info
        }

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "ClearedChargingLimit")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "ClearedChargingLimit")
    async def _handle_cleared_charging_limit_action(
        self, payload, msg_id, raw, text_data
    ):
        return await self._action_handler("ClearedChargingLimit").handle(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyReport")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyReport")
    async def _handle_notify_report_action(self, payload, msg_id, raw, text_data):
        payload_data = payload if isinstance(payload, dict) else {}
        generated_at = _parse_ocpp_timestamp(payload_data.get("generatedAt"))
        report_data = payload_data.get("reportData")
        request_id_value = payload_data.get("requestId")
        seq_no_value = payload_data.get("seqNo")
        tbc_value = payload_data.get("tbc")

        try:
            request_id = int(request_id_value) if request_id_value is not None else None
        except (TypeError, ValueError):
            request_id = None
        try:
            seq_no = int(seq_no_value) if seq_no_value is not None else None
        except (TypeError, ValueError):
            seq_no = None
        tbc = bool(tbc_value) if tbc_value is not None else False

        if generated_at is None:
            store.add_log(
                self.store_key,
                "NotifyReport ignored: missing generatedAt",
                log_type="charger",
            )
            return {}
        if not isinstance(report_data, (list, tuple)):
            store.add_log(
                self.store_key,
                "NotifyReport ignored: missing reportData",
                log_type="charger",
            )
            return {}

        def _persist_report() -> None:
            charger = None
            if self.charger and getattr(self.charger, "pk", None):
                charger = self.charger
            if charger is None and self.charger_id:
                charger = Charger.objects.filter(
                    charger_id=self.charger_id, connector_id=self.connector_value
                ).first()
            if charger is None and self.charger_id:
                charger, _created = Charger.objects.get_or_create(
                    charger_id=self.charger_id, connector_id=self.connector_value
                )
            if charger is None:
                return

            snapshot = DeviceInventorySnapshot.objects.create(
                charger=charger,
                request_id=request_id,
                seq_no=seq_no,
                generated_at=generated_at,
                tbc=tbc,
                raw_payload=payload_data,
            )

            for entry in report_data:
                if not isinstance(entry, dict):
                    continue
                component_data = (
                    entry.get("component")
                    if isinstance(entry.get("component"), dict)
                    else {}
                )
                variable_data = (
                    entry.get("variable")
                    if isinstance(entry.get("variable"), dict)
                    else {}
                )

                component_name = str(component_data.get("name") or "").strip()
                variable_name = str(variable_data.get("name") or "").strip()
                if not component_name or not variable_name:
                    continue

                component_instance = str(component_data.get("instance") or "").strip()
                variable_instance = str(variable_data.get("instance") or "").strip()

                attributes = entry.get("variableAttribute")
                if not isinstance(attributes, (list, tuple)):
                    attributes = []
                characteristics = entry.get("variableCharacteristics")
                if not isinstance(characteristics, dict):
                    characteristics = {}

                DeviceInventoryItem.objects.create(
                    snapshot=snapshot,
                    component_name=component_name,
                    component_instance=component_instance,
                    variable_name=variable_name,
                    variable_instance=variable_instance,
                    attributes=list(attributes),
                    characteristics=characteristics,
                )

        await database_sync_to_async(_persist_report)()

        details: list[str] = []
        if request_id is not None:
            details.append(f"requestId={request_id}")
        if seq_no is not None:
            details.append(f"seqNo={seq_no}")
        if generated_at is not None:
            details.append(f"generatedAt={generated_at.isoformat()}")
        details.append(f"items={len(report_data)}")

        store.add_log(
            self.store_key,
            "NotifyReport" + (": " + ", ".join(details) if details else ""),
            log_type="charger",
        )
        return {}

    def _log_ocpp201_notification(self, label: str, payload) -> None:
        message = label
        if payload:
            try:
                payload_text = json.dumps(payload, separators=(",", ":"))
            except (TypeError, ValueError):
                payload_text = str(payload)
            if payload_text and payload_text != "{}":
                message += f": {payload_text}"
        store.add_log(self.store_key, message, log_type="charger")

    def _log_notify_monitoring_report(
        self,
        *,
        request_id: int | None,
        seq_no: int | None,
        generated_at: datetime | None,
        tbc: bool,
        items: int,
    ) -> None:
        details: list[str] = []
        if request_id is not None:
            details.append(f"requestId={request_id}")
        if seq_no is not None:
            details.append(f"seqNo={seq_no}")
        if generated_at is not None:
            details.append(f"generatedAt={generated_at.isoformat()}")
        details.append(f"tbc={tbc}")
        details.append(f"items={items}")
        message = "NotifyMonitoringReport"
        if details:
            message += f": {', '.join(details)}"
        store.add_log(self.store_key, message, log_type="charger")

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "CostUpdated")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "CostUpdated")
    async def _handle_cost_updated_action(self, payload, msg_id, raw, text_data):
        self._log_ocpp201_notification("CostUpdated", payload)
        payload_data = payload if isinstance(payload, dict) else {}
        transaction_reference = str(payload_data.get("transactionId") or "").strip()
        total_cost_raw = payload_data.get("totalCost")
        currency_value = str(payload_data.get("currency") or "").strip()
        reported_at = _parse_ocpp_timestamp(payload_data.get("timestamp"))
        if reported_at is None:
            reported_at = timezone.now()

        try:
            total_cost_value = Decimal(str(total_cost_raw))
        except (InvalidOperation, TypeError, ValueError):
            store.add_log(
                self.store_key,
                "CostUpdated ignored: invalid totalCost",
                log_type="charger",
            )
            return {}

        if not transaction_reference:
            store.add_log(
                self.store_key,
                "CostUpdated ignored: missing transactionId",
                log_type="charger",
            )
            return {}

        tx_obj = store.transactions.get(self.store_key)
        if tx_obj is None and transaction_reference:
            tx_obj = await Transaction.aget_by_ocpp_id(
                self.charger, transaction_reference
            )
        if tx_obj is None and transaction_reference.isdigit():
            tx_obj = await database_sync_to_async(
                Transaction.objects.filter(
                    pk=int(transaction_reference), charger=self.charger
                ).first
            )()

        def _persist_cost_update():
            charger = self.charger
            if charger is None and self.charger_id:
                charger = Charger.objects.filter(
                    charger_id=self.charger_id, connector_id=None
                ).first()
            if charger is None:
                return None
            return CostUpdate.objects.create(
                charger=charger,
                transaction=tx_obj,
                ocpp_transaction_id=transaction_reference,
                connector_id=self.connector_value,
                total_cost=total_cost_value,
                currency=currency_value,
                payload=payload_data,
                reported_at=reported_at,
            )

        cost_update = await database_sync_to_async(_persist_cost_update)()
        if cost_update is not None:
            store.forward_cost_update_to_billing(
                {
                    "charger_id": cost_update.charger.charger_id,
                    "connector_id": cost_update.connector_id,
                    "transaction_id": transaction_reference,
                    "cost_update_id": cost_update.pk,
                    "total_cost": str(total_cost_value),
                    "currency": currency_value,
                    "reported_at": reported_at,
                }
            )
        return {}

    @protocol_call(
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "ReservationStatusUpdate",
    )
    @protocol_call(
        "ocpp21",
        ProtocolCallModel.CP_TO_CSMS,
        "ReservationStatusUpdate",
    )
    async def _handle_reservation_status_update_action(
        self, payload, msg_id, raw, text_data
    ):
        self._log_ocpp201_notification("ReservationStatusUpdate", payload)
        payload_data = payload if isinstance(payload, dict) else {}
        reservation_value = payload_data.get("reservationId")
        try:
            reservation_pk = (
                int(reservation_value) if reservation_value is not None else None
            )
        except (TypeError, ValueError):
            reservation_pk = None

        status_value = str(payload_data.get("reservationUpdateStatus") or "").strip()

        def _persist_reservation():
            reservation = None
            if reservation_pk is not None:
                charger_id_hint = getattr(self, "charger_id", None) or getattr(
                    getattr(self, "charger", None), "charger_id", None
                )
                connector_hint = getattr(self, "connector_value", None)
                reservation_query = CPReservation.objects.select_related(
                    "connector"
                ).filter(pk=reservation_pk)
                if charger_id_hint:
                    reservation_query = reservation_query.filter(
                        connector__charger_id=charger_id_hint
                    )
                if connector_hint is not None:
                    reservation_query = reservation_query.filter(
                        connector__connector_id=connector_hint
                    )
                reservation = reservation_query.first()
            if reservation is None:
                return None

            reservation.evcs_status = status_value
            reservation.evcs_error = ""
            confirmed = status_value.casefold() == "accepted"
            reservation.evcs_confirmed = confirmed
            reservation.evcs_confirmed_at = timezone.now() if confirmed else None
            reservation.save(
                update_fields=[
                    "evcs_status",
                    "evcs_error",
                    "evcs_confirmed",
                    "evcs_confirmed_at",
                    "updated_on",
                ]
            )
            return reservation

        reservation = await database_sync_to_async(_persist_reservation)()
        if reservation and reservation.connector_id:
            connector = reservation.connector
            store.forward_connector_release(
                {
                    "charger_id": connector.charger_id,
                    "connector_id": connector.connector_id,
                    "reservation_id": reservation.pk,
                    "status": status_value or None,
                }
            )

        return {}

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyChargingLimit")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyChargingLimit")
    async def _handle_notify_charging_limit_action(
        self, payload, msg_id, raw, text_data
    ):
        return await self._action_handler("NotifyChargingLimit").handle(
            payload, msg_id, raw, text_data
        )

    @protocol_call(
        "ocpp21",
        ProtocolCallModel.CP_TO_CSMS,
        "NotifyCustomerInformation",
    )
    @protocol_call(
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "NotifyCustomerInformation",
    )
    async def _handle_notify_customer_information_action(
        self, payload, msg_id, raw, text_data
    ):
        if not isinstance(payload, dict):
            store.add_log(
                self.store_key,
                "NotifyCustomerInformation: invalid payload received",
                log_type="charger",
            )
            return {}

        payload_data = payload
        request_id_value = payload_data.get("requestId")
        data_value = payload_data.get("data")
        tbc_value = payload_data.get("tbc")
        try:
            request_id = int(request_id_value) if request_id_value is not None else None
        except (TypeError, ValueError):
            request_id = None
        data_text = str(data_value or "").strip()
        tbc = bool(tbc_value) if tbc_value is not None else False
        notified_at = timezone.now()

        if request_id is None or not data_text:
            store.add_log(
                self.store_key,
                "NotifyCustomerInformation: missing requestId or data",
                log_type="charger",
            )
            return {}

        log_details = [f"requestId={request_id}", f"tbc={tbc}"]
        log_details.append(f"data={data_text}")
        store.add_log(
            self.store_key,
            "NotifyCustomerInformation: " + ", ".join(log_details),
            log_type="charger",
        )

        def _persist_customer_information() -> None:
            charger = self.charger
            if charger is None and self.charger_id:
                charger = Charger.objects.filter(
                    charger_id=self.charger_id,
                    connector_id=self.connector_value,
                ).first()
            if charger is None and self.charger_id:
                charger, _created = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=self.connector_value,
                )
            if charger is None:
                return

            request = None
            if request_id is not None:
                request = CustomerInformationRequest.objects.filter(
                    charger=charger, request_id=request_id
                ).first()
            if request is None and msg_id:
                request = CustomerInformationRequest.objects.filter(
                    charger=charger, ocpp_message_id=msg_id
                ).first()
            if request is None:
                request = CustomerInformationRequest.objects.create(
                    charger=charger,
                    ocpp_message_id=msg_id or "",
                    request_id=request_id,
                    payload=payload_data,
                )
            updates: dict[str, object] = {"last_notified_at": notified_at}
            if not tbc:
                updates["completed_at"] = notified_at
            CustomerInformationRequest.objects.filter(pk=request.pk).update(**updates)
            for field, value in updates.items():
                setattr(request, field, value)

            CustomerInformationChunk.objects.create(
                charger=charger,
                request_record=request,
                ocpp_message_id=msg_id or "",
                request_id=request_id,
                data=data_text,
                tbc=tbc,
                raw_payload=payload_data,
            )

            self._route_customer_care_acknowledgement(
                charger=charger,
                request_id=request_id,
                data_text=data_text,
                tbc=tbc,
                notified_at=notified_at,
            )

        await database_sync_to_async(_persist_customer_information)()
        return {}

    def _route_customer_care_acknowledgement(
        self,
        *,
        charger: Charger | None,
        request_id: int | None,
        data_text: str,
        tbc: bool,
        notified_at,
    ) -> None:
        if charger is None:
            return

        identifier_bits = [charger.charger_id or ""]
        if request_id is not None:
            identifier_bits.append(str(request_id))
        if charger.connector_id is not None:
            identifier_bits.append(str(charger.connector_id))
        workflow_identifier = (
            ":".join([bit for bit in identifier_bits if bit]) or "unknown"
        )

        logger.debug(
            "Customer-care acknowledgement recorded without flow transition: %s",
            workflow_identifier,
        )

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyDisplayMessages")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyDisplayMessages")
    async def _handle_notify_display_messages_action(
        self, payload, msg_id, raw, text_data
    ):
        return await self._action_handler("NotifyDisplayMessages").handle(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyEVChargingNeeds")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyEVChargingNeeds")
    async def _handle_notify_ev_charging_needs_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        evse_id_value = payload_data.get("evseId")
        charging_needs = payload_data.get("chargingNeeds")

        try:
            evse_id = int(evse_id_value) if evse_id_value is not None else None
        except (TypeError, ValueError):
            evse_id = None

        if not isinstance(charging_needs, dict) or evse_id is None:
            store.add_log(
                self.store_key,
                "NotifyEVChargingNeeds: missing evseId or chargingNeeds",
                log_type="charger",
            )
            return {}

        def _parse_energy(value: object | None) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        ac_params = charging_needs.get("acChargingParameters")
        if not isinstance(ac_params, dict):
            ac_params = {}
        dc_params = charging_needs.get("dcChargingParameters")
        if not isinstance(dc_params, dict):
            dc_params = {}

        requested_energy = _parse_energy(ac_params.get("energyAmount"))
        if requested_energy is None:
            requested_energy = _parse_energy(
                dc_params.get("maxEnergyAtChargingStation")
            )

        departure_time = _parse_ocpp_timestamp(charging_needs.get("departureTime"))
        received_at = timezone.now()

        log_parts = [f"evseId={evse_id}"]
        if requested_energy is not None:
            log_parts.append(f"energy={requested_energy}")
        if departure_time is not None:
            log_parts.append(f"departure={departure_time.isoformat()}")
        store.add_log(
            self.store_key,
            "NotifyEVChargingNeeds"
            + (": " + ", ".join(log_parts) if log_parts else ""),
            log_type="charger",
        )

        store.record_ev_charging_needs(
            getattr(self, "charger_id", None) or self.store_key,
            connector_id=getattr(self, "connector_value", None),
            evse_id=evse_id,
            requested_energy=requested_energy,
            departure_time=departure_time,
            charging_needs=charging_needs,
            received_at=received_at,
        )
        return {}

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyEVChargingSchedule")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyEVChargingSchedule")
    async def _handle_notify_ev_charging_schedule_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        evse_id_value = payload_data.get("evseId")
        charging_schedule = payload_data.get("chargingSchedule")
        timebase = _parse_ocpp_timestamp(payload_data.get("timebase"))

        try:
            evse_id = int(evse_id_value) if evse_id_value is not None else None
        except (TypeError, ValueError):
            evse_id = None

        if not isinstance(charging_schedule, dict) or evse_id is None:
            store.add_log(
                self.store_key,
                "NotifyEVChargingSchedule: missing evseId or chargingSchedule",
                log_type="charger",
            )
            return {}

        def _parse_int(value: object | None) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        duration_seconds = _parse_int(charging_schedule.get("duration"))
        schedule_id = _parse_int(charging_schedule.get("id"))
        charging_rate_unit = str(
            charging_schedule.get("chargingRateUnit") or ""
        ).strip()
        start_schedule = _parse_ocpp_timestamp(charging_schedule.get("startSchedule"))

        periods_data = charging_schedule.get("chargingSchedulePeriod")
        if not isinstance(periods_data, list):
            periods_data = []
        periods: list[dict[str, object]] = []
        for index, entry in enumerate(periods_data, start=1):
            if not isinstance(entry, dict):
                continue
            start_period = _parse_int(entry.get("startPeriod"))
            if start_period is None:
                continue
            try:
                limit = float(entry.get("limit"))
            except (TypeError, ValueError):
                continue
            period: dict[str, object] = {
                "start_period": start_period,
                "limit": limit,
            }
            number_phases = _parse_int(entry.get("numberPhases"))
            if number_phases is not None:
                period["number_phases"] = number_phases
            phase_to_use = _parse_int(entry.get("phaseToUse"))
            if phase_to_use is not None:
                period["phase_to_use"] = phase_to_use
            periods.append(period)

        normalized_schedule: dict[str, object] = {"periods": periods}
        if schedule_id is not None:
            normalized_schedule["id"] = schedule_id
        if duration_seconds is not None:
            normalized_schedule["duration_seconds"] = duration_seconds
        if charging_rate_unit:
            normalized_schedule["charging_rate_unit"] = charging_rate_unit
        if start_schedule is not None:
            normalized_schedule["start_schedule"] = start_schedule

        details: list[str] = [f"evseId={evse_id}"]
        if schedule_id is not None:
            details.append(f"id={schedule_id}")
        if periods:
            details.append(f"periods={len(periods)}")
        if timebase:
            details.append(f"timebase={timebase.isoformat()}")
        store.add_log(
            self.store_key,
            "NotifyEVChargingSchedule" + (": " + ", ".join(details) if details else ""),
            log_type="charger",
        )

        received_at = timezone.now()
        record = {
            "charger_id": getattr(self, "charger_id", None),
            "connector_id": getattr(self, "connector_value", None),
            "evse_id": evse_id,
            "timebase": timebase,
            "charging_schedule": normalized_schedule,
            "received_at": received_at,
        }

        store.record_ev_charging_schedule(
            record.get("charger_id"),
            connector_id=record.get("connector_id"),
            evse_id=evse_id,
            timebase=timebase,
            charging_schedule=normalized_schedule,
            received_at=received_at,
        )
        store.forward_ev_charging_schedule(record)
        return {}

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyMonitoringReport")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyMonitoringReport")
    async def _handle_notify_monitoring_report_action(
        self, payload, msg_id, raw, text_data
    ):
        return await self._action_handler("NotifyMonitoringReport").handle(
            payload, msg_id, raw, text_data
        )

    @protocol_call(
        "ocpp21",
        ProtocolCallModel.CP_TO_CSMS,
        "PublishFirmwareStatusNotification",
    )
    @protocol_call(
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "PublishFirmwareStatusNotification",
    )
    async def _handle_publish_firmware_status_notification_action_legacy(
        self, payload, msg_id, raw, text_data
    ):
        status_raw = payload.get("status")
        status_value = str(status_raw or "").strip()
        info_value = payload.get("statusInfo")
        if not isinstance(info_value, str):
            info_value = payload.get("info")
        status_info = str(info_value or "").strip()
        request_id_value = payload.get("requestId")
        timestamp_value = _parse_ocpp_timestamp(payload.get("publishTimestamp"))
        if timestamp_value is None:
            timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
        if timestamp_value is None:
            timestamp_value = timezone.now()

        def _persist_status():
            deployment = None
            try:
                deployment_pk = int(request_id_value)
            except (TypeError, ValueError, OverflowError):
                deployment_pk = None
            if deployment_pk:
                deployment = CPFirmwareDeployment.objects.filter(
                    pk=deployment_pk
                ).first()
            if deployment is None and self.charger:
                deployment = (
                    CPFirmwareDeployment.objects.filter(
                        charger=self.charger, completed_at__isnull=True
                    )
                    .order_by("-requested_at")
                    .first()
                )
            if deployment is None:
                return
            if status_value == "Downloaded" and deployment.downloaded_at is None:
                deployment.downloaded_at = timestamp_value
            deployment.mark_status(
                status_value,
                status_info,
                timestamp_value,
                response=payload,
            )

        await database_sync_to_async(_persist_status)()
        self._log_ocpp201_notification("PublishFirmwareStatusNotification", payload)
        return {}

    async def _handle_security_event_notification_action_legacy(
        self, payload, msg_id, raw, text_data
    ):
        event_type = str(payload.get("type") or payload.get("eventType") or "").strip()
        trigger_value = str(payload.get("trigger") or "").strip()
        timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
        if timestamp_value is None:
            timestamp_value = timezone.now()

        tech_raw = (
            payload.get("techInfo")
            or payload.get("techinfo")
            or payload.get("tech_info")
        )
        if isinstance(tech_raw, (dict, list)):
            tech_info = json.dumps(tech_raw, ensure_ascii=False)
        elif tech_raw is None:
            tech_info = ""
        else:
            tech_info = str(tech_raw)

        def _persist_security_event() -> None:
            connector_hint = payload.get("connectorId")
            target = None
            if connector_hint is not None:
                target = Charger.objects.filter(
                    charger_id=self.charger_id,
                    connector_id=connector_hint,
                ).first()
            if target is None:
                target = self.aggregate_charger or self.charger
            if target is None:
                return
            seq_raw = payload.get("seqNo") or payload.get("sequenceNumber")
            try:
                sequence_number = int(seq_raw) if seq_raw is not None else None
            except (TypeError, ValueError):
                sequence_number = None
            snapshot: dict[str, object]
            try:
                snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError):
                snapshot = {
                    str(key): (str(value) if value is not None else None)
                    for key, value in payload.items()
                }
            SecurityEvent.objects.create(
                charger=target,
                event_type=event_type or "Unknown",
                event_timestamp=timestamp_value,
                trigger=trigger_value,
                tech_info=tech_info,
                sequence_number=sequence_number,
                raw_payload=snapshot,
            )

        await database_sync_to_async(_persist_security_event)()
        label = event_type or "unknown"
        log_message = f"SecurityEventNotification: type={label}"
        if trigger_value:
            log_message += f", trigger={trigger_value}"
        store.add_log(self.store_key, log_message, log_type="charger")
        return {}

    @protocol_call(
        "ocpp16",
        ProtocolCallModel.CP_TO_CSMS,
        "DiagnosticsStatusNotification",
    )
    async def _handle_diagnostics_status_notification_action_legacy(
        self, payload, msg_id, raw, text_data
    ):
        status_value = payload.get("status")
        location_value = (
            payload.get("uploadLocation")
            or payload.get("location")
            or payload.get("uri")
        )
        timestamp_value = payload.get("timestamp")
        diagnostics_timestamp = None
        if timestamp_value:
            try:
                diagnostics_timestamp = parse_datetime(timestamp_value)
            except ValueError:
                pass
            if diagnostics_timestamp and timezone.is_naive(diagnostics_timestamp):
                diagnostics_timestamp = timezone.make_aware(
                    diagnostics_timestamp, timezone=timezone.utc
                )

        updates = {
            "diagnostics_status": status_value or None,
            "diagnostics_timestamp": diagnostics_timestamp,
            "diagnostics_location": location_value or None,
        }

        def _persist_diagnostics():
            targets: list[Charger] = []
            if self.charger:
                targets.append(self.charger)
            aggregate = self.aggregate_charger
            if aggregate and not any(
                target.pk == aggregate.pk for target in targets if target.pk
            ):
                targets.append(aggregate)
            for target in targets:
                for field, value in updates.items():
                    setattr(target, field, value)
                if target.pk:
                    Charger.objects.filter(pk=target.pk).update(**updates)

        await database_sync_to_async(_persist_diagnostics)()

        status_label = updates["diagnostics_status"] or "unknown"
        log_message = f"DiagnosticsStatusNotification: status={status_label}"
        if updates["diagnostics_timestamp"]:
            log_message += f", timestamp={updates['diagnostics_timestamp'].isoformat()}"
        if updates["diagnostics_location"]:
            log_message += f", location={updates['diagnostics_location']}"
        store.add_log(self.store_key, log_message, log_type="charger")
        if self.aggregate_charger and self.aggregate_charger.connector_id is None:
            aggregate_key = store.identity_key(self.charger_id, None)
            if aggregate_key != self.store_key:
                store.add_log(aggregate_key, log_message, log_type="charger")
        return {}

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "LogStatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "LogStatusNotification")
    async def _handle_log_status_notification_action_legacy(
        self, payload, msg_id, raw, text_data
    ):
        status_value = str(payload.get("status") or "").strip()
        log_type_value = str(payload.get("logType") or "").strip()
        request_identifier = payload.get("requestId")
        timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
        if timestamp_value is None:
            timestamp_value = timezone.now()
        location_value = str(
            payload.get("location") or payload.get("remoteLocation") or ""
        ).strip()
        filename_value = str(payload.get("filename") or "").strip()

        def _persist_log_status() -> str:
            qs = ChargerLogRequest.objects.filter(charger__charger_id=self.charger_id)
            request = None
            if request_identifier is not None:
                request = qs.filter(request_id=request_identifier).first()
            if request is None:
                request = qs.order_by("-requested_at").first()
            if request is None:
                charger = Charger.objects.filter(
                    charger_id=self.charger_id,
                    connector_id=None,
                ).first()
                if charger is None:
                    return ""
                creation_kwargs: dict[str, object] = {
                    "charger": charger,
                    "status": status_value or "",
                }
                if log_type_value:
                    creation_kwargs["log_type"] = log_type_value
                if request_identifier is not None:
                    creation_kwargs["request_id"] = request_identifier
                request = ChargerLogRequest.objects.create(**creation_kwargs)
                if timestamp_value is not None:
                    request.requested_at = timestamp_value
                    request.save(update_fields=["requested_at"])
            updates: dict[str, object] = {
                "last_status_at": timestamp_value,
                "last_status_payload": payload,
            }
            if status_value:
                updates["status"] = status_value
            if location_value:
                updates["location"] = location_value
            if filename_value:
                updates["filename"] = filename_value
            if log_type_value and not request.log_type:
                updates["log_type"] = log_type_value
            ChargerLogRequest.objects.filter(pk=request.pk).update(**updates)
            if updates.get("status"):
                request.status = str(updates["status"])
            if updates.get("location"):
                request.location = str(updates["location"])
            if updates.get("filename"):
                request.filename = str(updates["filename"])
            request.last_status_at = timestamp_value
            request.last_status_payload = payload
            if updates.get("log_type"):
                request.log_type = str(updates["log_type"])
            return request.session_key or ""

        session_capture = await database_sync_to_async(_persist_log_status)()
        status_label = status_value or "unknown"
        message = f"LogStatusNotification: status={status_label}"
        if request_identifier is not None:
            message += f", requestId={request_identifier}"
        if log_type_value:
            message += f", logType={log_type_value}"
        store.add_log(self.store_key, message, log_type="charger")
        if session_capture and status_value.lower() in {
            "uploaded",
            "uploadfailure",
            "rejected",
            "idle",
        }:
            store.finalize_log_capture(session_capture)
        return {}

    async def _handle_firmware_status_notification_action_legacy(
        self, payload, msg_id, raw, text_data
    ):
        status_raw = payload.get("status")
        status = str(status_raw or "").strip()
        info_value = payload.get("statusInfo")
        if not isinstance(info_value, str):
            info_value = payload.get("info")
        status_info = str(info_value or "").strip()
        timestamp_raw = payload.get("timestamp")
        timestamp_value = None
        if timestamp_raw:
            timestamp_value = parse_datetime(str(timestamp_raw))
            if timestamp_value and timezone.is_naive(timestamp_value):
                timestamp_value = timezone.make_aware(
                    timestamp_value, timezone.get_current_timezone()
                )
        if timestamp_value is None:
            timestamp_value = timezone.now()
        await self._update_firmware_state(status, status_info, timestamp_value)
        store.add_log(
            self.store_key,
            "FirmwareStatusNotification: " + json.dumps(payload, separators=(",", ":")),
            log_type="charger",
        )
        if self.aggregate_charger and self.aggregate_charger.connector_id is None:
            aggregate_key = store.identity_key(
                self.charger_id, self.aggregate_charger.connector_id
            )
            if aggregate_key != self.store_key:
                store.add_log(
                    aggregate_key,
                    "FirmwareStatusNotification: "
                    + json.dumps(payload, separators=(",", ":")),
                    log_type="charger",
                )
        return {}
