import asyncio
import json
import base64
import os
from datetime import datetime
from django.utils import timezone
from core.models import EnergyAccount, RFID as CoreRFID
import requests

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
        self.charger, created = await database_sync_to_async(
            Charger.objects.get_or_create
        )(
            charger_id=self.charger_id,
            connector_id=None,
            defaults={"last_path": self.scope.get("path", "")},
        )
        if created:
            await self._set_console_url(self.charger)
        location_name = await sync_to_async(
            lambda: self.charger.location.name if self.charger.location else ""
        )()
        store.register_log_name(
            self.charger_id, location_name or self.charger_id, log_type="charger"
        )

    async def _set_console_url(self, charger: Charger) -> None:
        ip = self.scope.get("client", ("", ""))[0]
        port = os.getenv("OCPP_EVCS_PORT", "8900")
        url = f"http://{ip}:{port}"
        try:
            await asyncio.to_thread(requests.get, url, timeout=5)
        except Exception:
            return
        await database_sync_to_async(Charger.objects.filter(pk=charger.pk).update)(
            console_url=url
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
        connector = str(connector)
        if self.charger.connector_id == connector:
            return
        existing = await database_sync_to_async(
            Charger.objects.filter(
                charger_id=self.charger_id, connector_id=connector
            ).first
        )()
        if existing:
            self.charger = existing
        elif self.charger.connector_id:
            self.charger = await database_sync_to_async(Charger.objects.create)(
                charger_id=self.charger_id,
                connector_id=connector,
                last_path=self.scope.get("path", ""),
            )
            await self._set_console_url(self.charger)
        else:
            self.charger.connector_id = connector
            await database_sync_to_async(self.charger.save)(
                update_fields=["connector_id"]
            )

    async def _store_meter_values(self, payload: dict, raw_message: str) -> None:
        """Parse a MeterValues payload into MeterValue rows."""
        connector = payload.get("connectorId")
        await self._assign_connector(connector)
        tx_id = payload.get("transactionId")
        tx_obj = None
        if tx_id is not None:
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
            if values and context not in ("Transaction.Begin", "Transaction.End"):
                readings.append(
                    MeterValue(
                        charger=self.charger,
                        connector_id=connector,
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
        if connector is not None and not self.charger.connector_id:
            self.charger.connector_id = str(connector)
            await database_sync_to_async(self.charger.save)(
                update_fields=["connector_id"]
            )
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
                        meter_start=payload.get("meterStart")
                        or payload.get("meterStop"),
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
