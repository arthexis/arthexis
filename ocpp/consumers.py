import base64
import ipaddress
import re
from datetime import datetime
import asyncio
import inspect
import json
import logging
from urllib.parse import parse_qs
from django.utils import timezone
from core.models import EnergyAccount, Reference, RFID as CoreRFID
from nodes.models import NetMessage
from django.core.exceptions import ValidationError

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from config.offline import requires_network

from . import store
from decimal import Decimal
from django.utils.dateparse import parse_datetime
from .models import (
    Transaction,
    Charger,
    ChargerConfiguration,
    MeterValue,
    DataTransferMessage,
)
from .reference_utils import host_is_local_loopback
from .evcs_discovery import (
    DEFAULT_CONSOLE_PORT,
    HTTPS_PORTS,
    build_console_url,
    prioritise_ports,
    scan_open_ports,
)

FORWARDED_PAIR_RE = re.compile(r"for=(?:\"?)(?P<value>[^;,\"\s]+)(?:\"?)", re.IGNORECASE)


logger = logging.getLogger(__name__)


# Query parameter keys that may contain the charge point serial. Keys are
# matched case-insensitively and trimmed before use.
SERIAL_QUERY_PARAM_NAMES = (
    "cid",
    "chargepointid",
    "charge_point_id",
    "chargeboxid",
    "charge_box_id",
    "chargerid",
)


def _parse_ip(value: str | None):
    """Return an :mod:`ipaddress` object for the provided value, if valid."""

    candidate = (value or "").strip()
    if not candidate or candidate.lower() == "unknown":
        return None
    if candidate.lower().startswith("for="):
        candidate = candidate[4:].strip()
    candidate = candidate.strip("'\"")
    if candidate.startswith("["):
        closing = candidate.find("]")
        if closing != -1:
            candidate = candidate[1:closing]
        else:
            candidate = candidate[1:]
    # Remove any comma separated values that may remain.
    if "," in candidate:
        candidate = candidate.split(",", 1)[0].strip()
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        host, sep, maybe_port = candidate.rpartition(":")
        if not sep or not maybe_port.isdigit():
            return None
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError:
            return None
    return parsed


def _resolve_client_ip(scope: dict) -> str | None:
    """Return the most useful client IP for the provided ASGI scope."""

    headers = scope.get("headers") or []
    header_map: dict[str, list[str]] = {}
    for key_bytes, value_bytes in headers:
        try:
            key = key_bytes.decode("latin1").lower()
        except Exception:
            continue
        try:
            value = value_bytes.decode("latin1")
        except Exception:
            value = ""
        header_map.setdefault(key, []).append(value)

    candidates: list[str] = []
    for raw in header_map.get("x-forwarded-for", []):
        candidates.extend(part.strip() for part in raw.split(","))
    for raw in header_map.get("forwarded", []):
        for segment in raw.split(","):
            match = FORWARDED_PAIR_RE.search(segment)
            if match:
                candidates.append(match.group("value"))
    candidates.extend(header_map.get("x-real-ip", []))
    client = scope.get("client")
    if client:
        candidates.append((client[0] or "").strip())

    fallback: str | None = None
    for raw in candidates:
        parsed = _parse_ip(raw)
        if not parsed:
            continue
        ip_text = str(parsed)
        if parsed.is_loopback:
            if fallback is None:
                fallback = ip_text
            continue
        return ip_text
    return fallback


def _parse_ocpp_timestamp(value) -> datetime | None:
    """Return an aware :class:`~datetime.datetime` for OCPP timestamps."""

    if not value:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = parse_datetime(str(value))
    if not timestamp:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


def _extract_vehicle_identifier(payload: dict) -> tuple[str, str]:
    """Return normalized VID and VIN values from an OCPP message payload."""

    raw_vid = payload.get("vid")
    vid_value = str(raw_vid).strip() if raw_vid is not None else ""
    raw_vin = payload.get("vin")
    vin_value = str(raw_vin).strip() if raw_vin is not None else ""
    if not vid_value and vin_value:
        vid_value = vin_value
    return vid_value, vin_value


class SinkConsumer(AsyncWebsocketConsumer):
    """Accept any message without validation."""

    @requires_network
    async def connect(self) -> None:
        self.client_ip = _resolve_client_ip(self.scope)
        if not store.register_ip_connection(self.client_ip, self):
            await self.close(code=4003)
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


class CSMSConsumer(AsyncWebsocketConsumer):
    """Very small subset of OCPP 1.6 CSMS behaviour."""

    consumption_update_interval = 300

    def _extract_serial_identifier(self) -> str:
        """Return the charge point serial from the query string or path."""

        self.serial_source = None
        query_bytes = self.scope.get("query_string") or b""
        self._raw_query_string = query_bytes.decode("utf-8", "ignore") if query_bytes else ""
        if query_bytes:
            try:
                parsed = parse_qs(
                    self._raw_query_string,
                    keep_blank_values=False,
                )
            except Exception:
                parsed = {}
            if parsed:
                normalized = {
                    key.lower(): values for key, values in parsed.items() if values
                }
                for candidate in SERIAL_QUERY_PARAM_NAMES:
                    values = normalized.get(candidate)
                    if not values:
                        continue
                    for value in values:
                        if not value:
                            continue
                        trimmed = value.strip()
                        if trimmed:
                            return trimmed
                          
        return self.scope["url_route"]["kwargs"].get("cid", "")

    @requires_network
    async def connect(self):
        raw_serial = self._extract_serial_identifier()
        try:
            self.charger_id = Charger.validate_serial(raw_serial)
        except ValidationError as exc:
            serial = Charger.normalize_serial(raw_serial)
            store_key = store.pending_key(serial)
            message = exc.messages[0] if exc.messages else "Invalid Serial Number"
            details: list[str] = []
            if getattr(self, "serial_source", None):
                details.append(f"serial_source={self.serial_source}")
            if getattr(self, "_raw_query_string", ""):
                details.append(f"query_string={self._raw_query_string!r}")
            if details:
                message = f"{message} ({'; '.join(details)})"
            store.add_log(
                store_key,
                f"Rejected connection: {message}",
                log_type="charger",
            )
            await self.close(code=4003)
            return
        self.connector_value: int | None = None
        self.store_key = store.pending_key(self.charger_id)
        self.aggregate_charger: Charger | None = None
        self._consumption_task: asyncio.Task | None = None
        self._consumption_message_uuid: str | None = None
        subprotocol = None
        offered = self.scope.get("subprotocols", [])
        if "ocpp1.6" in offered:
            subprotocol = "ocpp1.6"
        self.client_ip = _resolve_client_ip(self.scope)
        self._header_reference_created = False
        # Close any pending connection for this charger so reconnections do
        # not leak stale consumers when the connector id has not been
        # negotiated yet.
        existing = store.connections.get(self.store_key)
        if existing is not None:
            store.release_ip_connection(getattr(existing, "client_ip", None), existing)
            await existing.close()
        if not store.register_ip_connection(self.client_ip, self):
            store.add_log(
                self.store_key,
                f"Rejected connection from {self.client_ip or 'unknown'}: rate limit exceeded",
                log_type="charger",
            )
            await self.close(code=4003)
            return
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
        await database_sync_to_async(self.charger.refresh_manager_node)()
        self.aggregate_charger = self.charger
        location_name = await sync_to_async(
            lambda: self.charger.location.name if self.charger.location else ""
        )()
        friendly_name = location_name or self.charger_id
        store.register_log_name(self.store_key, friendly_name, log_type="charger")
        store.register_log_name(self.charger_id, friendly_name, log_type="charger")
        store.register_log_name(
            store.identity_key(self.charger_id, None),
            friendly_name,
            log_type="charger",
        )

    async def _get_account(self, id_tag: str) -> EnergyAccount | None:
        """Return the energy account for the provided RFID if valid."""
        if not id_tag:
            return None

        def _resolve() -> EnergyAccount | None:
            matches = CoreRFID.matching_queryset(id_tag).filter(allowed=True)
            if not matches.exists():
                return None
            return (
                EnergyAccount.objects.filter(rfids__in=matches)
                .distinct()
                .first()
            )

        return await database_sync_to_async(_resolve)()

    async def _ensure_rfid_seen(self, id_tag: str) -> CoreRFID | None:
        """Ensure an RFID record exists and update its last seen timestamp."""

        if not id_tag:
            return None

        normalized = id_tag.upper()

        def _ensure() -> CoreRFID:
            now = timezone.now()
            tag, _created = CoreRFID.register_scan(normalized)
            updates = []
            if not tag.allowed:
                tag.allowed = True
                updates.append("allowed")
            if tag.last_seen_on != now:
                tag.last_seen_on = now
                updates.append("last_seen_on")
            if updates:
                tag.save(update_fields=updates)
            return tag

        return await database_sync_to_async(_ensure)()

    def _log_unlinked_rfid(self, rfid: str) -> None:
        """Record a warning when an RFID is authorized without an account."""

        message = (
            f"Authorized RFID {rfid} on charger {self.charger_id} without linked energy account"
        )
        logger.warning(message)
        store.add_log(
            store.pending_key(self.charger_id),
            message,
            log_type="charger",
        )

    async def _assign_connector(self, connector: int | str | None) -> None:
        """Ensure ``self.charger`` matches the provided connector id."""
        if connector in (None, "", "-"):
            connector_value = None
        else:
            try:
                connector_value = int(connector)
                if connector_value == 0:
                    connector_value = None
            except (TypeError, ValueError):
                return
        if connector_value is None:
            aggregate = self.aggregate_charger
            if (
                not aggregate
                or aggregate.connector_id is not None
                or aggregate.charger_id != self.charger_id
            ):
                aggregate, _ = await database_sync_to_async(
                    Charger.objects.get_or_create
                )(
                    charger_id=self.charger_id,
                    connector_id=None,
                    defaults={"last_path": self.scope.get("path", "")},
                )
                await database_sync_to_async(aggregate.refresh_manager_node)()
                self.aggregate_charger = aggregate
            self.charger = self.aggregate_charger
            previous_key = self.store_key
            new_key = store.identity_key(self.charger_id, None)
            if previous_key != new_key:
                existing_consumer = store.connections.get(new_key)
                if existing_consumer is not None and existing_consumer is not self:
                    await existing_consumer.close()
                store.reassign_identity(previous_key, new_key)
                store.connections[new_key] = self
                store.logs["charger"].setdefault(new_key, [])
            aggregate_name = await sync_to_async(
                lambda: self.charger.name or self.charger.charger_id
            )()
            friendly_name = aggregate_name or self.charger_id
            store.register_log_name(new_key, friendly_name, log_type="charger")
            store.register_log_name(
                store.identity_key(self.charger_id, None),
                friendly_name,
                log_type="charger",
            )
            store.register_log_name(self.charger_id, friendly_name, log_type="charger")
            self.store_key = new_key
            self.connector_value = None
            if not self._header_reference_created and self.client_ip:
                await database_sync_to_async(self._ensure_console_reference)()
                self._header_reference_created = True
            return
        if (
            self.connector_value == connector_value
            and self.charger.connector_id == connector_value
        ):
            return
        if (
            not self.aggregate_charger
            or self.aggregate_charger.connector_id is not None
        ):
            aggregate, _ = await database_sync_to_async(
                Charger.objects.get_or_create
            )(
                charger_id=self.charger_id,
                connector_id=None,
                defaults={"last_path": self.scope.get("path", "")},
            )
            await database_sync_to_async(aggregate.refresh_manager_node)()
            self.aggregate_charger = aggregate
        existing = await database_sync_to_async(
            Charger.objects.filter(
                charger_id=self.charger_id, connector_id=connector_value
            ).first
        )()
        if existing:
            self.charger = existing
            await database_sync_to_async(self.charger.refresh_manager_node)()
        else:

            def _create_connector():
                charger, _ = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=connector_value,
                    defaults={"last_path": self.scope.get("path", "")},
                )
                if self.scope.get("path") and charger.last_path != self.scope.get(
                    "path"
                ):
                    charger.last_path = self.scope.get("path")
                    charger.save(update_fields=["last_path"])
                charger.refresh_manager_node()
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
                lambda: self.aggregate_charger.name or self.aggregate_charger.charger_id
            )()
        store.register_log_name(
            store.identity_key(self.charger_id, None),
            aggregate_name or self.charger_id,
            log_type="charger",
        )
        self.store_key = new_key
        self.connector_value = connector_value

    def _ensure_console_reference(self) -> None:
        """Create or update a header reference for the connected charger."""

        ip = (self.client_ip or "").strip()
        serial = (self.charger_id or "").strip()
        if not ip or not serial:
            return
        if host_is_local_loopback(ip):
            return
        host = ip
        ports = scan_open_ports(host)
        if ports:
            ordered_ports = prioritise_ports(ports)
        else:
            ordered_ports = prioritise_ports([DEFAULT_CONSOLE_PORT])
        port = ordered_ports[0] if ordered_ports else DEFAULT_CONSOLE_PORT
        secure = port in HTTPS_PORTS
        url = build_console_url(host, port, secure)
        alt_text = f"{serial} Console"
        reference = Reference.objects.filter(alt_text=alt_text).order_by("id").first()
        if reference is None:
            reference = Reference.objects.create(
                alt_text=alt_text,
                value=url,
                show_in_header=True,
                method="link",
            )
        updated_fields: list[str] = []
        if reference.value != url:
            reference.value = url
            updated_fields.append("value")
        if reference.method != "link":
            reference.method = "link"
            updated_fields.append("method")
        if not reference.show_in_header:
            reference.show_in_header = True
            updated_fields.append("show_in_header")
        if updated_fields:
            reference.save(update_fields=updated_fields)

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
                        if tx_obj and field == "energy" and tx_obj.meter_start is None:
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

    async def _cancel_consumption_message(self) -> None:
        """Stop any scheduled consumption message updates."""

        task = self._consumption_task
        self._consumption_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._consumption_message_uuid = None

    async def _update_consumption_message(self, tx_id: int) -> str | None:
        """Create or update the Net Message for an active transaction."""

        existing_uuid = self._consumption_message_uuid

        def _persist() -> str | None:
            tx = (
                Transaction.objects.select_related("charger")
                .filter(pk=tx_id)
                .first()
            )
            if not tx:
                return None
            charger = tx.charger or self.charger
            serial = ""
            if charger and charger.charger_id:
                serial = charger.charger_id
            elif self.charger_id:
                serial = self.charger_id
            serial = serial[:64]
            if not serial:
                return None
            now_local = timezone.localtime(timezone.now())
            body_value = f"{tx.kw:.1f} kWh {now_local.strftime('%H:%M')}"[:256]
            if existing_uuid:
                msg = NetMessage.objects.filter(uuid=existing_uuid).first()
                if msg:
                    msg.subject = serial
                    msg.body = body_value
                    msg.save(update_fields=["subject", "body"])
                    msg.propagate()
                    return str(msg.uuid)
            msg = NetMessage.broadcast(subject=serial, body=body_value)
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
            pass
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

        configuration = ChargerConfiguration.objects.create(
            charger_identifier=self.charger_id,
            connector_id=connector_value,
            configuration_keys=normalized_entries,
            unknown_keys=unknown_values,
            raw_payload=raw_payload,
        )
        Charger.objects.filter(charger_id=self.charger_id).update(
            configuration=configuration
        )
        return configuration

    async def _handle_call_result(self, message_id: str, payload: dict | None) -> None:
        metadata = store.pop_pending_call(message_id)
        if not metadata:
            return
        if metadata.get("charger_id") and metadata.get("charger_id") != self.charger_id:
            return
        action = metadata.get("action")
        log_key = metadata.get("log_key") or self.store_key
        payload_data = payload if isinstance(payload, dict) else {}
        if action == "DataTransfer":
            message_pk = metadata.get("message_pk")
            if not message_pk:
                store.record_pending_call_result(
                    message_id,
                    metadata=metadata,
                    payload=payload_data,
                )
                return

            def _apply():
                message = DataTransferMessage.objects.filter(pk=message_pk).first()
                if not message:
                    return
                status_value = str((payload or {}).get("status") or "").strip()
                message.status = status_value
                message.response_data = (payload or {}).get("data")
                message.error_code = ""
                message.error_description = ""
                message.error_details = None
                message.responded_at = timezone.now()
                message.save(
                    update_fields=[
                        "status",
                        "response_data",
                        "error_code",
                        "error_description",
                        "error_details",
                        "responded_at",
                        "updated_at",
                    ]
                )

            await database_sync_to_async(_apply)()
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "GetConfiguration":
            try:
                payload_text = json.dumps(
                    payload_data, sort_keys=True, ensure_ascii=False
                )
            except TypeError:
                payload_text = str(payload_data)
            store.add_log(
                log_key,
                f"GetConfiguration result: {payload_text}",
                log_type="charger",
            )
            configuration = await database_sync_to_async(
                self._persist_configuration_result
            )(payload_data, metadata.get("connector_id"))
            if configuration:
                if self.charger and self.charger.charger_id == self.charger_id:
                    self.charger.configuration = configuration
                if (
                    self.aggregate_charger
                    and self.aggregate_charger.charger_id == self.charger_id
                ):
                    self.aggregate_charger.configuration = configuration
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "TriggerMessage":
            status_value = str(payload_data.get("status") or "").strip()
            target = metadata.get("trigger_target") or metadata.get("follow_up_action")
            connector_value = metadata.get("trigger_connector")
            message = "TriggerMessage result"
            if target:
                message = f"TriggerMessage {target} result"
            if status_value:
                message += f": status={status_value}"
            if connector_value:
                message += f", connector={connector_value}"
            store.add_log(log_key, message, log_type="charger")
            if status_value == "Accepted" and target:
                store.register_triggered_followup(
                    self.charger_id,
                    str(target),
                    connector=connector_value,
                    log_key=log_key,
                    target=str(target),
                )
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "RemoteStartTransaction":
            status_value = str(payload_data.get("status") or "").strip()
            message = "RemoteStartTransaction result"
            if status_value:
                message += f": status={status_value}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "RemoteStopTransaction":
            status_value = str(payload_data.get("status") or "").strip()
            message = "RemoteStopTransaction result"
            if status_value:
                message += f": status={status_value}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "Reset":
            status_value = str(payload_data.get("status") or "").strip()
            message = "Reset result"
            if status_value:
                message += f": status={status_value}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action != "ChangeAvailability":
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        status = str((payload or {}).get("status") or "").strip()
        requested_type = metadata.get("availability_type")
        connector_value = metadata.get("connector_id")
        requested_at = metadata.get("requested_at")
        await self._update_change_availability_state(
            connector_value,
            requested_type,
            status,
            requested_at,
            details="",
        )
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
    ) -> None:
        metadata = store.pop_pending_call(message_id)
        if not metadata:
            return
        if metadata.get("charger_id") and metadata.get("charger_id") != self.charger_id:
            return
        action = metadata.get("action")
        log_key = metadata.get("log_key") or self.store_key
        if action == "DataTransfer":
            message_pk = metadata.get("message_pk")
            if not message_pk:
                store.record_pending_call_result(
                    message_id,
                    metadata=metadata,
                    success=False,
                    error_code=error_code,
                    error_description=description,
                    error_details=details,
                )
                return

            def _apply():
                message = DataTransferMessage.objects.filter(pk=message_pk).first()
                if not message:
                    return
                status_value = (error_code or "Error").strip() or "Error"
                message.status = status_value
                message.response_data = None
                message.error_code = (error_code or "").strip()
                message.error_description = (description or "").strip()
                message.error_details = details
                message.responded_at = timezone.now()
                message.save(
                    update_fields=[
                        "status",
                        "response_data",
                        "error_code",
                        "error_description",
                        "error_details",
                        "responded_at",
                        "updated_at",
                    ]
                )

            await database_sync_to_async(_apply)()
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        if action == "GetConfiguration":
            parts: list[str] = []
            code_text = (error_code or "").strip()
            if code_text:
                parts.append(f"code={code_text}")
            description_text = (description or "").strip()
            if description_text:
                parts.append(f"description={description_text}")
            if details:
                try:
                    details_text = json.dumps(details, sort_keys=True, ensure_ascii=False)
                except TypeError:
                    details_text = str(details)
                if details_text:
                    parts.append(f"details={details_text}")
            if parts:
                message = "GetConfiguration error: " + ", ".join(parts)
            else:
                message = "GetConfiguration error"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        if action == "TriggerMessage":
            target = metadata.get("trigger_target") or metadata.get("follow_up_action")
            connector_value = metadata.get("trigger_connector")
            parts: list[str] = []
            if error_code:
                parts.append(f"code={str(error_code).strip()}")
            if description:
                parts.append(f"description={str(description).strip()}")
            if details:
                try:
                    parts.append(
                        "details="
                        + json.dumps(details, sort_keys=True, ensure_ascii=False)
                    )
                except TypeError:
                    parts.append(f"details={details}")
            label = f"TriggerMessage {target}" if target else "TriggerMessage"
            message = label + " error"
            if parts:
                message += ": " + ", ".join(parts)
            if connector_value:
                message += f", connector={connector_value}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        if action == "RemoteStartTransaction":
            message = "RemoteStartTransaction error"
            if error_code:
                message += f": code={str(error_code).strip()}"
            if description:
                suffix = str(description).strip()
                if suffix:
                    message += f", description={suffix}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        if action == "RemoteStopTransaction":
            message = "RemoteStopTransaction error"
            if error_code:
                message += f": code={str(error_code).strip()}"
            if description:
                suffix = str(description).strip()
                if suffix:
                    message += f", description={suffix}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        if action == "Reset":
            message = "Reset error"
            if error_code:
                message += f": code={str(error_code).strip()}"
            if description:
                suffix = str(description).strip()
                if suffix:
                    message += f", description={suffix}"
            store.add_log(log_key, message, log_type="charger")
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        if action != "ChangeAvailability":
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
        detail_text = (description or "").strip()
        if not detail_text and details:
            try:
                detail_text = json.dumps(details, sort_keys=True)
            except Exception:
                detail_text = str(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        requested_type = metadata.get("availability_type")
        connector_value = metadata.get("connector_id")
        requested_at = metadata.get("requested_at")
        await self._update_change_availability_state(
            connector_value,
            requested_type,
            "Rejected",
            requested_at,
            details=detail_text,
        )
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            success=False,
            error_code=error_code,
            error_description=description,
            error_details=details,
        )

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
                    defaults={"last_path": self.scope.get("path", "")},
                )
                return charger
            charger, _ = Charger.objects.get_or_create(
                charger_id=self.charger_id,
                connector_id=connector_value,
                defaults={"last_path": self.scope.get("path", "")},
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
            filters: dict[str, object] = {"charger_id": self.charger_id}
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

    async def _update_availability_state(
        self,
        state: str,
        timestamp: datetime,
        connector_value: int | None,
    ) -> None:
        def _apply():
            filters: dict[str, object] = {"charger_id": self.charger_id}
            if connector_value is None:
                filters["connector_id__isnull"] = True
            else:
                filters["connector_id"] = connector_value
            updates = {
                "availability_state": state,
                "availability_state_updated_at": timestamp,
            }
            targets = list(Charger.objects.filter(**filters))
            if not targets:
                return
            Charger.objects.filter(pk__in=[target.pk for target in targets]).update(
                **updates
            )
            for target in targets:
                for field, value in updates.items():
                    setattr(target, field, value)
                if self.charger and self.charger.pk == target.pk:
                    for field, value in updates.items():
                        setattr(self.charger, field, value)
                if self.aggregate_charger and self.aggregate_charger.pk == target.pk:
                    for field, value in updates.items():
                        setattr(self.aggregate_charger, field, value)

        await database_sync_to_async(_apply)()

    async def disconnect(self, close_code):
        store.release_ip_connection(getattr(self, "client_ip", None), self)
        tx_obj = None
        if self.charger_id:
            tx_obj = store.get_transaction(self.charger_id, self.connector_value)
        if tx_obj:
            await self._update_consumption_message(tx_obj.pk)
        await self._cancel_consumption_message()
        store.connections.pop(self.store_key, None)
        pending_key = store.pending_key(self.charger_id)
        if self.store_key != pending_key:
            store.connections.pop(pending_key, None)
        store.end_session_log(self.store_key)
        store.stop_session_lock()
        store.clear_pending_calls(self.charger_id)
        store.add_log(self.store_key, f"Closed (code={close_code})", log_type="charger")

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
        if not isinstance(msg, list) or not msg:
            return
        message_type = msg[0]
        if message_type == 2:
            msg_id, action = msg[1], msg[2]
            payload = msg[3] if len(msg) > 3 else {}
            reply_payload = {}
            connector_hint = None
            if isinstance(payload, dict):
                connector_hint = payload.get("connectorId")
            follow_up = store.consume_triggered_followup(
                self.charger_id, action, connector_hint
            )
            if follow_up:
                follow_up_log_key = follow_up.get("log_key") or self.store_key
                target_label = follow_up.get("target") or action
                connector_slug_value = follow_up.get("connector")
                suffix = ""
                if (
                    connector_slug_value
                    and connector_slug_value != store.AGGREGATE_SLUG
                ):
                    suffix = f" (connector {connector_slug_value})"
                store.add_log(
                    follow_up_log_key,
                    f"TriggerMessage follow-up received: {target_label}{suffix}",
                    log_type="charger",
                )
            await self._assign_connector(payload.get("connectorId"))
            if action == "BootNotification":
                reply_payload = {
                    "currentTime": datetime.utcnow().isoformat() + "Z",
                    "interval": 300,
                    "status": "Accepted",
                }
            elif action == "DataTransfer":
                reply_payload = await self._handle_data_transfer(msg_id, payload)
            elif action == "Heartbeat":
                reply_payload = {"currentTime": datetime.utcnow().isoformat() + "Z"}
                now = timezone.now()
                self.charger.last_heartbeat = now
                if (
                    self.aggregate_charger
                    and self.aggregate_charger is not self.charger
                ):
                    self.aggregate_charger.last_heartbeat = now
                await database_sync_to_async(
                    Charger.objects.filter(charger_id=self.charger_id).update
                )(last_heartbeat=now)
            elif action == "StatusNotification":
                await self._assign_connector(payload.get("connectorId"))
                status = (payload.get("status") or "").strip()
                error_code = (payload.get("errorCode") or "").strip()
                vendor_info = {
                    key: value
                    for key, value in (
                        ("info", payload.get("info")),
                        ("vendorId", payload.get("vendorId")),
                    )
                    if value
                }
                vendor_value = vendor_info or None
                timestamp_raw = payload.get("timestamp")
                status_timestamp = (
                    parse_datetime(timestamp_raw) if timestamp_raw else None
                )
                if status_timestamp is None:
                    status_timestamp = timezone.now()
                elif timezone.is_naive(status_timestamp):
                    status_timestamp = timezone.make_aware(status_timestamp)
                update_kwargs = {
                    "last_status": status,
                    "last_error_code": error_code,
                    "last_status_vendor_info": vendor_value,
                    "last_status_timestamp": status_timestamp,
                }

                def _update_instance(instance: Charger | None) -> None:
                    if not instance:
                        return
                    instance.last_status = status
                    instance.last_error_code = error_code
                    instance.last_status_vendor_info = vendor_value
                    instance.last_status_timestamp = status_timestamp

                await database_sync_to_async(
                    Charger.objects.filter(
                        charger_id=self.charger_id, connector_id=None
                    ).update
                )(**update_kwargs)
                connector_value = self.connector_value
                if connector_value is not None:
                    await database_sync_to_async(
                        Charger.objects.filter(
                            charger_id=self.charger_id,
                            connector_id=connector_value,
                        ).update
                    )(**update_kwargs)
                _update_instance(self.aggregate_charger)
                _update_instance(self.charger)
                if connector_value is not None and status.lower() == "available":
                    tx_obj = store.transactions.pop(self.store_key, None)
                    if tx_obj:
                        await self._cancel_consumption_message()
                        store.end_session_log(self.store_key)
                        store.stop_session_lock()
                store.add_log(
                    self.store_key,
                    f"StatusNotification processed: {json.dumps(payload, sort_keys=True)}",
                    log_type="charger",
                )
                availability_state = Charger.availability_state_from_status(status)
                if availability_state:
                    await self._update_availability_state(
                        availability_state, status_timestamp, self.connector_value
                    )
                reply_payload = {}
            elif action == "Authorize":
                id_tag = payload.get("idTag")
                account = await self._get_account(id_tag)
                status = "Invalid"
                if self.charger.require_rfid:
                    tag = None
                    tag_created = False
                    if id_tag:
                        tag, tag_created = await database_sync_to_async(
                            CoreRFID.register_scan
                        )(id_tag)
                    if account:
                        if await database_sync_to_async(account.can_authorize)():
                            status = "Accepted"
                    elif (
                        id_tag
                        and tag
                        and not tag_created
                        and tag.allowed
                    ):
                        status = "Accepted"
                        self._log_unlinked_rfid(tag.rfid)
                else:
                    await self._ensure_rfid_seen(id_tag)
                    status = "Accepted"
                reply_payload = {"idTagInfo": {"status": status}}
            elif action == "MeterValues":
                await self._store_meter_values(payload, text_data)
                self.charger.last_meter_values = payload
                await database_sync_to_async(
                    Charger.objects.filter(pk=self.charger.pk).update
                )(last_meter_values=payload)
                reply_payload = {}
            elif action == "DiagnosticsStatusNotification":
                status_value = payload.get("status")
                location_value = (
                    payload.get("uploadLocation")
                    or payload.get("location")
                    or payload.get("uri")
                )
                timestamp_value = payload.get("timestamp")
                diagnostics_timestamp = None
                if timestamp_value:
                    diagnostics_timestamp = parse_datetime(timestamp_value)
                    if diagnostics_timestamp and timezone.is_naive(
                        diagnostics_timestamp
                    ):
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
                    if (
                        aggregate
                        and not any(
                            target.pk == aggregate.pk for target in targets if target.pk
                        )
                    ):
                        targets.append(aggregate)
                    for target in targets:
                        for field, value in updates.items():
                            setattr(target, field, value)
                        if target.pk:
                            Charger.objects.filter(pk=target.pk).update(**updates)

                await database_sync_to_async(_persist_diagnostics)()

                status_label = updates["diagnostics_status"] or "unknown"
                log_message = "DiagnosticsStatusNotification: status=%s" % (
                    status_label,
                )
                if updates["diagnostics_timestamp"]:
                    log_message += ", timestamp=%s" % (
                        updates["diagnostics_timestamp"].isoformat()
                    )
                if updates["diagnostics_location"]:
                    log_message += ", location=%s" % updates["diagnostics_location"]
                store.add_log(self.store_key, log_message, log_type="charger")
                if self.aggregate_charger and self.aggregate_charger.connector_id is None:
                    aggregate_key = store.identity_key(self.charger_id, None)
                    if aggregate_key != self.store_key:
                        store.add_log(aggregate_key, log_message, log_type="charger")
                reply_payload = {}
            elif action == "StartTransaction":
                id_tag = payload.get("idTag")
                tag = None
                tag_created = False
                if id_tag:
                    tag, tag_created = await database_sync_to_async(
                        CoreRFID.register_scan
                    )(id_tag)
                account = await self._get_account(id_tag)
                if id_tag and not self.charger.require_rfid:
                    seen_tag = await self._ensure_rfid_seen(id_tag)
                    if seen_tag:
                        tag = seen_tag
                await self._assign_connector(payload.get("connectorId"))
                authorized = True
                authorized_via_tag = False
                if self.charger.require_rfid:
                    if account is not None:
                        authorized = await database_sync_to_async(
                            account.can_authorize
                        )()
                    elif (
                        id_tag
                        and tag
                        and not tag_created
                        and getattr(tag, "allowed", False)
                    ):
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
                    store.transactions[self.store_key] = tx_obj
                    store.start_session_log(self.store_key, tx_obj.pk)
                    store.start_session_lock()
                    store.add_session_message(self.store_key, text_data)
                    await self._start_consumption_updates(tx_obj)
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
                    received_start = timezone.now()
                    vid_value, vin_value = _extract_vehicle_identifier(payload)
                    tx_obj = await database_sync_to_async(Transaction.objects.create)(
                        pk=tx_id,
                        charger=self.charger,
                        start_time=received_start,
                        received_start_time=received_start,
                        meter_start=payload.get("meterStart")
                        or payload.get("meterStop"),
                        vid=vid_value,
                        vin=vin_value,
                    )
                if tx_obj:
                    stop_timestamp = _parse_ocpp_timestamp(payload.get("timestamp"))
                    received_stop = timezone.now()
                    tx_obj.meter_stop = payload.get("meterStop")
                    vid_value, vin_value = _extract_vehicle_identifier(payload)
                    if vid_value:
                        tx_obj.vid = vid_value
                    if vin_value:
                        tx_obj.vin = vin_value
                    tx_obj.stop_time = stop_timestamp or received_stop
                    tx_obj.received_stop_time = received_stop
                    await database_sync_to_async(tx_obj.save)()
                    await self._update_consumption_message(tx_obj.pk)
                await self._cancel_consumption_message()
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
        elif message_type == 3:
            msg_id = msg[1] if len(msg) > 1 else ""
            payload = msg[2] if len(msg) > 2 else {}
            await self._handle_call_result(msg_id, payload)
        elif message_type == 4:
            msg_id = msg[1] if len(msg) > 1 else ""
            error_code = msg[2] if len(msg) > 2 else ""
            description = msg[3] if len(msg) > 3 else ""
            details = msg[4] if len(msg) > 4 else {}
            await self._handle_call_error(msg_id, error_code, description, details)
