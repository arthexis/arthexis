import asyncio
import json
from datetime import datetime
from django.utils import timezone
from django.contrib.auth import get_user_model

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from . import store
from .models import Transaction, Charger


class CSMSConsumer(AsyncWebsocketConsumer):
    """Very small subset of OCPP 1.6 CSMS behaviour."""

    async def connect(self):
        self.charger_id = self.scope["url_route"]["kwargs"].get("cid", "")
        await self.accept()
        store.connections[self.charger_id] = self
        store.logs.setdefault(self.charger_id, [])
        self.charger, _ = await database_sync_to_async(Charger.objects.get_or_create)(
            charger_id=self.charger_id
        )

    async def _valid_idtag(self, id_tag: str) -> bool:
        if not self.charger.require_rfid:
            return True
        if not id_tag:
            return False
        User = get_user_model()
        return await database_sync_to_async(User.objects.filter(rfid_uid=id_tag).exists)()

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
                status = (
                    "Accepted" if await self._valid_idtag(payload.get("idTag")) else "Invalid"
                )
                reply_payload = {"idTagInfo": {"status": "Accepted"}}
            elif action == "MeterValues":
                await database_sync_to_async(
                    Charger.objects.filter(charger_id=self.charger_id).update
                )(last_meter_values=payload)
                reply_payload = {}
            elif action == "StartTransaction":
                tx_id = int(datetime.utcnow().timestamp())
                tx_obj = await database_sync_to_async(Transaction.objects.create)(
                    charger_id=self.charger_id,
                    transaction_id=tx_id,
                    meter_start=payload.get("meterStart"),
                    start_time=timezone.now(),
                )
                reply_payload = {"idTagInfo": {"status": status}}
            elif action == "StartTransaction":
                if await self._valid_idtag(payload.get("idTag")):
                    tx_id = int(datetime.utcnow().timestamp())
                    tx_obj = await database_sync_to_async(Transaction.objects.create)(
                        charger_id=self.charger_id,
                        transaction_id=tx_id,
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
