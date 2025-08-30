import asyncio
import json
import base64
from datetime import datetime
from django.utils import timezone
from core.models import Account

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from config.offline import requires_network

from . import store
from decimal import Decimal
from django.utils.dateparse import parse_datetime
from .models import Transaction, Charger, MeterReading


class SinkConsumer(AsyncWebsocketConsumer):
    """Accept any message without validation."""

    @requires_network
    async def connect(self) -> None:
        await self.accept()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
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
        subprotocol = None
        offered = self.scope.get("subprotocols", [])
        if "ocpp1.6" in offered:
            subprotocol = "ocpp1.6"
        # If a connection for this charger already exists, close it so a new
        # simulator session can start immediately.
        existing = store.connections.get(self.charger_id)
        if existing is not None:
            await existing.close()
        await self.accept(subprotocol=subprotocol)
        store.add_log(
            self.charger_id,
            f"Connected (subprotocol={subprotocol or 'none'})",
            log_type="charger",
        )
        store.connections[self.charger_id] = self
        store.logs["charger"].setdefault(self.charger_id, [])
        self.charger, _ = await database_sync_to_async(
            Charger.objects.update_or_create
        )(
            charger_id=self.charger_id,
            defaults={"last_path": self.scope.get("path", "")},
        )
        location_name = await sync_to_async(
            lambda: self.charger.location.name if self.charger.location else ""
        )()
        store.register_log_name(
            self.charger_id, location_name or self.charger_id, log_type="charger"
        )

    async def _get_account(self, id_tag: str) -> Account | None:
        """Return the account for the provided RFID if valid."""
        if not id_tag:
            return None
        return await database_sync_to_async(
            Account.objects.filter(
                rfids__rfid=id_tag.upper(), rfids__allowed=True
            ).first
        )()

    async def _store_meter_values(self, payload: dict, raw_message: str) -> None:
        """Parse a MeterValues payload into MeterReading rows."""
        connector = payload.get("connectorId")
        tx_id = payload.get("transactionId")
        tx_obj = None
        if tx_id is not None:
            # Look up an existing transaction, first in the in-memory store
            # then in the database.  If none exists create one so that meter
            # readings can be linked to it.
            tx_obj = store.transactions.get(self.charger_id)
            if not tx_obj or tx_obj.pk != int(tx_id):
                tx_obj = await database_sync_to_async(
                    Transaction.objects.filter(pk=tx_id, charger=self.charger).first
                )()
            if tx_obj is None:
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    pk=tx_id, charger=self.charger, start_time=timezone.now()
                )
                store.start_session_log(self.charger_id, tx_obj.pk)
                store.add_session_message(self.charger_id, raw_message)
            store.transactions[self.charger_id] = tx_obj
        else:
            tx_obj = store.transactions.get(self.charger_id)

        readings = []
        start_updated = False
        temperature = None
        temp_unit = ""
        for mv in payload.get("meterValue", []):
            ts = parse_datetime(mv.get("timestamp"))
            for sv in mv.get("sampledValue", []):
                try:
                    val = Decimal(str(sv.get("value")))
                except Exception:
                    continue
                if (
                    tx_obj
                    and tx_obj.meter_start is None
                    and sv.get("measurand", "") in ("", "Energy.Active.Import.Register")
                ):
                    try:
                        mult = 1000 if sv.get("unit") == "kW" else 1
                        tx_obj.meter_start = int(val * mult)
                        start_updated = True
                    except Exception:
                        pass
                measurand = sv.get("measurand", "")
                unit = sv.get("unit", "")
                if measurand == "Temperature":
                    temperature = val
                    temp_unit = unit
                readings.append(
                    MeterReading(
                        charger=self.charger,
                        connector_id=connector,
                        transaction=tx_obj,
                        timestamp=ts,
                        measurand=measurand,
                        value=val,
                        unit=unit,
                    )
                )
        if readings:
            await database_sync_to_async(MeterReading.objects.bulk_create)(readings)
            if tx_obj and start_updated:
                await database_sync_to_async(tx_obj.save)(update_fields=["meter_start"])
        if connector is not None and not self.charger.connector_id:
            self.charger.connector_id = str(connector)
            await database_sync_to_async(self.charger.save)(update_fields=["connector_id"])
        if temperature is not None:
            self.charger.temperature = temperature
            self.charger.temperature_unit = temp_unit
            await database_sync_to_async(self.charger.save)(
                update_fields=["temperature", "temperature_unit"]
            )

    async def disconnect(self, close_code):
        store.connections.pop(self.charger_id, None)
        store.end_session_log(self.charger_id)
        store.add_log(
            self.charger_id, f"Closed (code={close_code})", log_type="charger"
        )

    async def receive(self, text_data=None, bytes_data=None):
        raw = text_data
        if raw is None and bytes_data is not None:
            raw = base64.b64encode(bytes_data).decode("ascii")
        if raw is None:
            return
        store.add_log(self.charger_id, raw, log_type="charger")
        store.add_session_message(self.charger_id, raw)
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(msg, list) and msg and msg[0] == 2:
            msg_id, action = msg[1], msg[2]
            payload = msg[3] if len(msg) > 3 else {}
            reply_payload = {}
            if action == "BootNotification":
                reply_payload = {
                    "currentTime": datetime.utcnow().isoformat() + "Z",
                    "interval": 300,
                    "status": "Accepted",
                }
            elif action == "Heartbeat":
                reply_payload = {
                    "currentTime": datetime.utcnow().isoformat() + "Z"
                }
                now = timezone.now()
                self.charger.last_heartbeat = now
                await database_sync_to_async(
                    Charger.objects.filter(charger_id=self.charger_id).update
                )(last_heartbeat=now)
            elif action == "Authorize":
                account = await self._get_account(payload.get("idTag"))
                if self.charger.require_rfid:
                    status = (
                        "Accepted"
                        if account and await database_sync_to_async(account.can_authorize)()
                        else "Invalid"
                    )
                else:
                    status = "Accepted"
                reply_payload = {"idTagInfo": {"status": status}}
            elif action == "MeterValues":
                await self._store_meter_values(payload, text_data)
                self.charger.last_meter_values = payload
                await database_sync_to_async(
                    Charger.objects.filter(charger_id=self.charger_id).update
                )(last_meter_values=payload)
                reply_payload = {}
            elif action == "StartTransaction":
                account = await self._get_account(payload.get("idTag"))
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
                        rfid=(payload.get("idTag") or ""),
                        vin=(payload.get("vin") or ""),
                        meter_start=payload.get("meterStart"),
                        start_time=timezone.now(),
                    )
                    store.transactions[self.charger_id] = tx_obj
                    store.start_session_log(self.charger_id, tx_obj.pk)
                    store.add_session_message(self.charger_id, text_data)
                    reply_payload = {
                        "transactionId": tx_obj.pk,
                        "idTagInfo": {"status": "Accepted"},
                    }
                else:
                    reply_payload = {"idTagInfo": {"status": "Invalid"}}
            elif action == "StopTransaction":
                tx_id = payload.get("transactionId")
                tx_obj = store.transactions.pop(self.charger_id, None)
                if not tx_obj and tx_id is not None:
                    tx_obj = await database_sync_to_async(
                        Transaction.objects.filter(pk=tx_id, charger=self.charger).first
                    )()
                if not tx_obj and tx_id is not None:
                    tx_obj = await database_sync_to_async(Transaction.objects.create)(
                        pk=tx_id,
                        charger=self.charger,
                        start_time=timezone.now(),
                        meter_start=payload.get("meterStart") or payload.get("meterStop"),
                        vin=(payload.get("vin") or ""),
                    )
                if tx_obj:
                    tx_obj.meter_stop = payload.get("meterStop")
                    tx_obj.stop_time = timezone.now()
                    await database_sync_to_async(tx_obj.save)()
                reply_payload = {"idTagInfo": {"status": "Accepted"}}
                store.end_session_log(self.charger_id)
            response = [3, msg_id, reply_payload]
            await self.send(json.dumps(response))
            store.add_log(
                self.charger_id, f"< {json.dumps(response)}", log_type="charger"
            )
