import asyncio
import json
from datetime import datetime
from django.utils import timezone
from accounts.models import Account

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
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
        await self.accept(subprotocol=subprotocol)
        store.connections[self.charger_id] = self
        store.logs.setdefault(self.charger_id, [])
        self.charger, _ = await database_sync_to_async(
            Charger.objects.update_or_create
        )(
            charger_id=self.charger_id,
            defaults={"last_path": self.scope.get("path", "")},
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

    async def _store_meter_values(self, payload: dict) -> None:
        """Parse a MeterValues payload into MeterReading rows."""
        connector = payload.get("connectorId")
        tx_id = payload.get("transactionId")
        readings = []
        for mv in payload.get("meterValue", []):
            ts = parse_datetime(mv.get("timestamp"))
            for sv in mv.get("sampledValue", []):
                try:
                    val = Decimal(str(sv.get("value")))
                except Exception:
                    continue
                readings.append(
                    MeterReading(
                        charger=self.charger,
                        connector_id=connector,
                        transaction_id=tx_id,
                        timestamp=ts,
                        measurand=sv.get("measurand", ""),
                        value=val,
                        unit=sv.get("unit", ""),
                    )
                )
        if readings:
            await database_sync_to_async(MeterReading.objects.bulk_create)(readings)

    async def disconnect(self, close_code):
        store.connections.pop(self.charger_id, None)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None:
            return
        store.logs.setdefault(self.charger_id, []).append(f"> {text_data}")
        try:
            msg = json.loads(text_data)
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
                await database_sync_to_async(
                    Charger.objects.filter(charger_id=self.charger_id).update
                )(last_heartbeat=timezone.now())
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
                await self._store_meter_values(payload)
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
                    tx_id = int(datetime.utcnow().timestamp())
                    tx_obj = await database_sync_to_async(Transaction.objects.create)(
                        charger_id=self.charger_id,
                        transaction_id=tx_id,
                        account=account,
                        meter_start=payload.get("meterStart"),
                        start_time=timezone.now(),
                    )
                    store.transactions[self.charger_id] = tx_obj
                    reply_payload = {
                        "transactionId": tx_id,
                        "idTagInfo": {"status": "Accepted"},
                    }
                else:
                    reply_payload = {"idTagInfo": {"status": "Invalid"}}
            elif action == "StopTransaction":
                tx_obj = store.transactions.pop(self.charger_id, None)
                if tx_obj:
                    tx_obj.meter_stop = payload.get("meterStop")
                    tx_obj.stop_time = timezone.now()
                    await database_sync_to_async(tx_obj.save)()
                reply_payload = {"idTagInfo": {"status": "Accepted"}}
            response = [3, msg_id, reply_payload]
            await self.send(json.dumps(response))
            store.logs.setdefault(self.charger_id, []).append(f"< {json.dumps(response)}")
