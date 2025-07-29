import asyncio
import json
from datetime import datetime
from django.utils import timezone
from accounts.models import RFID, Account

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
        tag = await database_sync_to_async(
            RFID.objects.filter(
                rfid=id_tag.upper(), allowed=True, user__isnull=False
            )
            .select_related("user__account")
            .first
        )()
        if tag and hasattr(tag.user, "account"):
            return tag.user.account
        return None

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
