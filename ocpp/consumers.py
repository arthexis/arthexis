import json
import base64
from datetime import datetime
from django.utils import timezone
from core.models import EnergyAccount, RFID as CoreRFID
from nodes.models import NetMessage

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from config.offline import requires_network

from . import store
from decimal import Decimal
from django.utils.dateparse import parse_datetime
from .models import Transaction, Charger, MeterValue


class SinkConsumer(AsyncWebsocketConsumer):
    """Accept any message without validation."""

    @requires_network
    async def connect(self) -> None:
        await self.accept()

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


class CSMSConsumer(AsyncWebsocketConsumer):
    """Very small subset of OCPP 1.6 CSMS behaviour."""

    @requires_network
    async def connect(self):
        self.charger_id = self.scope["url_route"]["kwargs"].get("cid", "")
        self.connector_value: int | None = None
        self.store_key = store.pending_key(self.charger_id)
        self.aggregate_charger: Charger | None = None
        subprotocol = None
        offered = self.scope.get("subprotocols", [])
        if "ocpp1.6" in offered:
            subprotocol = "ocpp1.6"
        # Close any pending connection for this charger so reconnections do
        # not leak stale consumers when the connector id has not been
        # negotiated yet.
        existing = store.connections.get(self.store_key)
        if existing is not None:
            await existing.close()
        await self.accept(subprotocol=subprotocol)
        store.add_log(
            self.store_key,
            f"Connected (subprotocol={subprotocol or 'none'})",
            log_type="charger",
        )
        store.connections[self.store_key] = self
        store.logs["charger"].setdefault(self.store_key, [])
        self.charger, created = await database_sync_to_async(
            Charger.objects.get_or_create
        )(
            charger_id=self.charger_id,
            connector_id=None,
            defaults={"last_path": self.scope.get("path", "")},
        )
        self.aggregate_charger = self.charger
        location_name = await sync_to_async(
            lambda: self.charger.location.name if self.charger.location else ""
        )()
        friendly_name = location_name or self.charger_id
        store.register_log_name(self.store_key, friendly_name, log_type="charger")
        store.register_log_name(
            self.charger_id, friendly_name, log_type="charger"
        )
        store.register_log_name(
            store.identity_key(self.charger_id, None),
            friendly_name,
            log_type="charger",
        )

    async def _get_account(self, id_tag: str) -> EnergyAccount | None:
        """Return the energy account for the provided RFID if valid."""
        if not id_tag:
            return None
        return await database_sync_to_async(
            EnergyAccount.objects.filter(
                rfids__rfid=id_tag.upper(), rfids__allowed=True
            ).first
        )()

    async def _assign_connector(self, connector: int | str | None) -> None:
        """Ensure ``self.charger`` matches the provided connector id."""
        if connector is None:
            return
        try:
            connector_value = int(connector)
        except (TypeError, ValueError):
            return
        if (
            self.connector_value == connector_value
            and self.charger.connector_id == connector_value
        ):
            return
        if not self.aggregate_charger or self.aggregate_charger.connector_id is not None:
            self.aggregate_charger = await database_sync_to_async(
                Charger.objects.get_or_create
            )(
                charger_id=self.charger_id,
                connector_id=None,
                defaults={"last_path": self.scope.get("path", "")},
            )[0]
        existing = await database_sync_to_async(
            Charger.objects.filter(
                charger_id=self.charger_id, connector_id=connector_value
            ).first
        )()
        if existing:
            self.charger = existing
        else:
            def _create_connector():
                charger, _ = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=connector_value,
                    defaults={"last_path": self.scope.get("path", "")},
                )
                if self.scope.get("path") and charger.last_path != self.scope.get("path"):
                    charger.last_path = self.scope.get("path")
                    charger.save(update_fields=["last_path"])
                return charger

            self.charger = await database_sync_to_async(_create_connector)()
        previous_key = self.store_key
        new_key = store.identity_key(self.charger_id, connector_value)
        if previous_key != new_key:
            existing_consumer = store.connections.get(new_key)
            if existing_consumer is not None and existing_consumer is not self:
                await existing_consumer.close()
            store.reassign_identity(previous_key, new_key)
            store.connections[new_key] = self
            store.logs["charger"].setdefault(new_key, [])
        connector_name = await sync_to_async(
            lambda: self.charger.name or self.charger.charger_id
        )()
        store.register_log_name(new_key, connector_name, log_type="charger")
        aggregate_name = ""
        if self.aggregate_charger:
            aggregate_name = await sync_to_async(
                lambda: self.aggregate_charger.name
                or self.aggregate_charger.charger_id
            )()
        store.register_log_name(
            store.identity_key(self.charger_id, None),
            aggregate_name or self.charger_id,
            log_type="charger",
        )
        self.store_key = new_key
        self.connector_value = connector_value

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
        tx_obj = None
        if tx_id is not None:
            tx_obj = store.transactions.get(self.store_key)
            if not tx_obj or tx_obj.pk != int(tx_id):
                tx_obj = await database_sync_to_async(
                    Transaction.objects.filter(pk=tx_id, charger=self.charger).first
                )()
            if tx_obj is None:
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    pk=tx_id, charger=self.charger, start_time=timezone.now()
                )
                store.start_session_log(self.store_key, tx_obj.pk)
                store.add_session_message(self.store_key, raw_message)
            store.transactions[self.store_key] = tx_obj
        else:
            tx_obj = store.transactions.get(self.store_key)

        readings = []
        updated_fields: set[str] = set()
        temperature = None
        temp_unit = ""
        for mv in payload.get("meterValue", []):
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
                field = None
                if measurand in ("", "Energy.Active.Import.Register"):
                    field = "energy"
                    if unit == "Wh":
                        val = val / Decimal("1000")
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
                            mult = 1000 if unit in ("kW", "kWh") else 1
                            setattr(tx_obj, f"meter_{suffix}", int(val * mult))
                            updated_fields.add(f"meter_{suffix}")
                        else:
                            setattr(tx_obj, f"{field}_{suffix}", val)
                            updated_fields.add(f"{field}_{suffix}")
                    else:
                        values[field] = val
                        if (
                            tx_obj
                            and field == "energy"
                            and tx_obj.meter_start is None
                        ):
                            mult = 1000 if unit in ("kW", "kWh") else 1
                            try:
                                tx_obj.meter_start = int(val * mult)
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

    async def _broadcast_charging_started(self) -> None:
        """Send a network message announcing a charging session."""

        def _message_payload() -> dict[str, str] | None:
            charger = self.charger
            aggregate = self.aggregate_charger
            if not charger:
                return None
            location_name = ""
            if charger.location_id:
                location_name = charger.location.name
            elif aggregate and aggregate.location_id:
                location_name = aggregate.location.name
            cid_value = charger.connector_slug if charger.connector_id is not None else Charger.AGGREGATE_CONNECTOR_SLUG
            return {
                "location": location_name,
                "sn": charger.charger_id,
                "cid": str(cid_value),
            }

        payload = await database_sync_to_async(_message_payload)()
        if not payload:
            return
        try:
            await database_sync_to_async(NetMessage.broadcast)(
                subject="charging-started",
                body=json.dumps(payload, separators=(",", ":")),
            )
        except Exception as exc:  # pragma: no cover - logging of unexpected errors
            store.add_log(
                self.store_key,
                f"Failed to broadcast charging start: {exc}",
                log_type="charger",
            )

    async def disconnect(self, close_code):
        store.connections.pop(self.store_key, None)
        pending_key = store.pending_key(self.charger_id)
        if self.store_key != pending_key:
            store.connections.pop(pending_key, None)
        store.end_session_log(self.store_key)
        store.stop_session_lock()
        store.add_log(
            self.store_key, f"Closed (code={close_code})", log_type="charger"
        )

    async def receive(self, text_data=None, bytes_data=None):
        raw = text_data
        if raw is None and bytes_data is not None:
            raw = base64.b64encode(bytes_data).decode("ascii")
        if raw is None:
            return
        store.add_log(self.store_key, raw, log_type="charger")
        store.add_session_message(self.store_key, raw)
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(msg, list) and msg and msg[0] == 2:
            msg_id, action = msg[1], msg[2]
            payload = msg[3] if len(msg) > 3 else {}
            reply_payload = {}
            await self._assign_connector(payload.get("connectorId"))
            if action == "BootNotification":
                reply_payload = {
                    "currentTime": datetime.utcnow().isoformat() + "Z",
                    "interval": 300,
                    "status": "Accepted",
                }
            elif action == "Heartbeat":
                reply_payload = {"currentTime": datetime.utcnow().isoformat() + "Z"}
                now = timezone.now()
                self.charger.last_heartbeat = now
                await database_sync_to_async(
                    Charger.objects.filter(pk=self.charger.pk).update
                )(last_heartbeat=now)
            elif action == "Authorize":
                account = await self._get_account(payload.get("idTag"))
                if self.charger.require_rfid:
                    status = (
                        "Accepted"
                        if account
                        and await database_sync_to_async(account.can_authorize)()
                        else "Invalid"
                    )
                else:
                    status = "Accepted"
                reply_payload = {"idTagInfo": {"status": status}}
            elif action == "MeterValues":
                await self._store_meter_values(payload, text_data)
                self.charger.last_meter_values = payload
                await database_sync_to_async(
                    Charger.objects.filter(pk=self.charger.pk).update
                )(last_meter_values=payload)
                reply_payload = {}
            elif action == "StartTransaction":
                id_tag = payload.get("idTag")
                account = await self._get_account(id_tag)
                if id_tag:
                    await database_sync_to_async(CoreRFID.objects.get_or_create)(
                        rfid=id_tag.upper()
                    )
                await self._assign_connector(payload.get("connectorId"))
                if self.charger.require_rfid:
                    authorized = (
                        account is not None
                        and await database_sync_to_async(account.can_authorize)()
                    )
                else:
                    authorized = True
                if authorized:
                    tx_obj = await database_sync_to_async(Transaction.objects.create)(
                        charger=self.charger,
                        account=account,
                        rfid=(id_tag or ""),
                        vin=(payload.get("vin") or ""),
                        connector_id=payload.get("connectorId"),
                        meter_start=payload.get("meterStart"),
                        start_time=timezone.now(),
                    )
                    store.transactions[self.store_key] = tx_obj
                    store.start_session_log(self.store_key, tx_obj.pk)
                    store.start_session_lock()
                    store.add_session_message(self.store_key, text_data)
                    await self._broadcast_charging_started()
                    reply_payload = {
                        "transactionId": tx_obj.pk,
                        "idTagInfo": {"status": "Accepted"},
                    }
                else:
                    reply_payload = {"idTagInfo": {"status": "Invalid"}}
            elif action == "StopTransaction":
                tx_id = payload.get("transactionId")
                tx_obj = store.transactions.pop(self.store_key, None)
                if not tx_obj and tx_id is not None:
                    tx_obj = await database_sync_to_async(
                        Transaction.objects.filter(pk=tx_id, charger=self.charger).first
                    )()
                if not tx_obj and tx_id is not None:
                    tx_obj = await database_sync_to_async(Transaction.objects.create)(
                        pk=tx_id,
                        charger=self.charger,
                        start_time=timezone.now(),
                        meter_start=payload.get("meterStart")
                        or payload.get("meterStop"),
                        vin=(payload.get("vin") or ""),
                    )
                if tx_obj:
                    tx_obj.meter_stop = payload.get("meterStop")
                    tx_obj.stop_time = timezone.now()
                    await database_sync_to_async(tx_obj.save)()
                reply_payload = {"idTagInfo": {"status": "Accepted"}}
                store.end_session_log(self.store_key)
                store.stop_session_lock()
            elif action == "FirmwareStatusNotification":
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
                await self._update_firmware_state(
                    status, status_info, timestamp_value
                )
                store.add_log(
                    self.store_key,
                    "FirmwareStatusNotification: "
                    + json.dumps(payload, separators=(",", ":")),
                    log_type="charger",
                )
                if (
                    self.aggregate_charger
                    and self.aggregate_charger.connector_id is None
                ):
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
                reply_payload = {}
            response = [3, msg_id, reply_payload]
            await self.send(json.dumps(response))
            store.add_log(
                self.store_key, f"< {json.dumps(response)}", log_type="charger"
            )
