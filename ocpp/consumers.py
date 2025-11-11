import base64
import ipaddress
import re
from datetime import datetime
import asyncio
from collections import deque
import inspect
import json
import logging
import uuid
from urllib.parse import parse_qs
from django.conf import settings
from django.utils import timezone
from core.models import CustomerAccount, Reference, RFID as CoreRFID
from nodes.models import NetMessage
from django.core.exceptions import ValidationError

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from config.offline import requires_network

from . import store
from .forwarding_service import (
    get_session as get_forwarding_session,
    remove_session as remove_forwarding_session,
    sync_forwarded_charge_points,
)
from decimal import Decimal
from django.utils.dateparse import parse_datetime
from .models import (
    Transaction,
    Charger,
    ChargerConfiguration,
    MeterValue,
    DataTransferMessage,
    CPReservation,
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    RFIDSessionAttempt,
    SecurityEvent,
    ChargerLogRequest,
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


# Default vendor identifier used when requesting firmware via DataTransfer for
# chargers that do not yet have stored firmware records.
DEFAULT_FIRMWARE_VENDOR_ID = "org.openchargealliance.firmware"


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

        serial = self.scope["url_route"]["kwargs"].get("cid", "")
        if serial:
            return serial

        path = (self.scope.get("path") or "").strip("/")
        if not path:
            return ""

        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return ""

        return segments[-1]

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
        self._initial_metadata_task: asyncio.Task | None = None
        subprotocol = None
        offered = self.scope.get("subprotocols", [])
        # Operational safeguard: never reject a charger solely because it omits
        # or sends an unexpected subprotocol.  We negotiate ``ocpp1.6`` when the
        # charger offers it, but otherwise continue without a subprotocol so we
        # accept as many real-world stations as possible.
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
        store.logs["charger"].setdefault(
            self.store_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
        )
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

        fetch_configuration = False
        fetch_firmware = False
        if not created:

            def _check_existing_metadata() -> tuple[bool, bool]:
                has_configuration = ChargerConfiguration.objects.filter(
                    charger_identifier=self.charger_id
                ).exists()
                charger_pk = getattr(self.charger, "pk", None)
                has_firmware = False
                if charger_pk:
                    has_firmware = CPFirmware.objects.filter(
                        source_charger_id=charger_pk
                    ).exists()
                return has_configuration, has_firmware

            has_configuration, has_firmware = await database_sync_to_async(
                _check_existing_metadata
            )()
            fetch_configuration = not has_configuration
            fetch_firmware = not has_firmware

        if fetch_configuration or fetch_firmware:
            log_key = self.store_key
            task = asyncio.create_task(
                self._fetch_initial_metadata(
                    log_key=log_key,
                    fetch_configuration=fetch_configuration,
                    fetch_firmware=fetch_firmware,
                )
            )
            task.add_done_callback(
                lambda _: setattr(self, "_initial_metadata_task", None)
            )
            self._initial_metadata_task = task
        if not created:
            await database_sync_to_async(sync_forwarded_charge_points)(
                refresh_forwarders=False
            )

    async def _get_account(self, id_tag: str) -> CustomerAccount | None:
        """Return the customer account for the provided RFID if valid."""
        if not id_tag:
            return None

        def _resolve() -> CustomerAccount | None:
            matches = CoreRFID.matching_queryset(id_tag).filter(allowed=True)
            if not matches.exists():
                return None
            return (
                CustomerAccount.objects.filter(rfids__in=matches)
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
            if not tag.released:
                tag.released = True
                updates.append("released")
            if tag.last_seen_on != now:
                tag.last_seen_on = now
                updates.append("last_seen_on")
            if updates:
                tag.save(update_fields=sorted(set(updates)))
            return tag

        return await database_sync_to_async(_ensure)()

    def _log_unlinked_rfid(self, rfid: str) -> None:
        """Record a warning when an RFID is authorized without an account."""

        message = (
            f"Authorized RFID {rfid} on charger {self.charger_id} without linked customer account"
        )
        logger.warning(message)
        store.add_log(
            store.pending_key(self.charger_id),
            message,
            log_type="charger",
        )

    async def _record_rfid_attempt(
        self,
        *,
        rfid: str,
        status: RFIDSessionAttempt.Status,
        account: CustomerAccount | None,
        transaction: Transaction | None = None,
    ) -> None:
        """Persist RFID session attempt metadata for reporting."""

        normalized = (rfid or "").strip().upper()
        if not normalized:
            return

        charger = self.charger

        def _create_attempt() -> None:
            RFIDSessionAttempt.objects.create(
                charger=charger,
                rfid=normalized,
                status=status,
                account=account,
                transaction=transaction,
            )

        await database_sync_to_async(_create_attempt)()

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
                store.logs["charger"].setdefault(
                    new_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
                )
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
            store.logs["charger"].setdefault(
                new_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
            )
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

    async def _ensure_forwarding_context(
        self, charger,
    ) -> tuple[tuple[str, ...], int | None] | None:
        """Return forwarding configuration for ``charger`` when available."""

        if not charger or not getattr(charger, "forwarded_to_id", None):
            return None

        def _resolve():
            from protocols.models import CPForwarder

            target_id = getattr(charger, "forwarded_to_id", None)
            if not target_id:
                return None
            qs = CPForwarder.objects.filter(target_node_id=target_id, enabled=True)
            source_id = getattr(charger, "node_origin_id", None)
            forwarder = None
            if source_id:
                forwarder = qs.filter(source_node_id=source_id).first()
            if forwarder is None:
                forwarder = qs.filter(source_node__isnull=True).first()
            if forwarder is None:
                forwarder = qs.first()
            if forwarder is None:
                return None
            messages = tuple(forwarder.get_forwarded_messages())
            return messages, forwarder.pk

        return await database_sync_to_async(_resolve)()

    async def _record_forwarding_activity(
        self,
        *,
        charger_pk: int | None,
        forwarder_pk: int | None,
        timestamp: datetime,
    ) -> None:
        """Persist forwarding activity metadata for the provided charger."""

        if charger_pk is None and forwarder_pk is None:
            return

        def _update():
            if charger_pk:
                Charger.objects.filter(pk=charger_pk).update(
                    forwarding_watermark=timestamp
                )
            if forwarder_pk:
                from protocols.models import CPForwarder

                CPForwarder.objects.filter(pk=forwarder_pk).update(
                    last_forwarded_at=timestamp,
                    is_running=True,
                )

        await database_sync_to_async(_update)()

    async def _forward_charge_point_message(self, action: str, raw: str) -> None:
        """Forward an OCPP message to the configured remote node when permitted."""

        if not action or not raw:
            return

        charger = self.aggregate_charger or self.charger
        if charger is None or not getattr(charger, "pk", None):
            return
        session = get_forwarding_session(charger.pk)
        if session is None or not session.is_connected:
            return

        allowed = getattr(session, "forwarded_messages", None)
        forwarder_pk = getattr(session, "forwarder_id", None)
        if allowed is None or (forwarder_pk is None and charger.forwarded_to_id):
            context = await self._ensure_forwarding_context(charger)
            if context is None:
                return
            allowed, forwarder_pk = context
            session.forwarded_messages = allowed
            session.forwarder_id = forwarder_pk

        if allowed is not None and action not in allowed:
            return

        try:
            await sync_to_async(session.connection.send)(raw)
        except Exception as exc:  # pragma: no cover - network errors
            logger.warning(
                "Failed to forward %s from charger %s: %s",
                action,
                getattr(charger, "charger_id", charger.pk),
                exc,
            )
            remove_forwarding_session(charger.pk)
            return

        timestamp = timezone.now()
        await self._record_forwarding_activity(
            charger_pk=charger.pk,
            forwarder_pk=forwarder_pk,
            timestamp=timestamp,
        )
        charger.forwarding_watermark = timestamp
        aggregate = self.aggregate_charger
        if aggregate and aggregate.pk == charger.pk:
            aggregate.forwarding_watermark = timestamp
        current = self.charger
        if current and current.pk == charger.pk and current is not aggregate:
            current.forwarding_watermark = timestamp

    async def _fetch_initial_metadata(
        self,
        *,
        log_key: str,
        fetch_configuration: bool,
        fetch_firmware: bool,
    ) -> None:
        """Request configuration and firmware snapshots when missing."""

        if not (fetch_configuration or fetch_firmware):
            return
        connector_value = self.connector_value
        try:
            if fetch_configuration:
                await self._request_configuration_snapshot(
                    log_key=log_key, connector_value=connector_value
                )
            if fetch_firmware:
                await self._request_firmware_snapshot(
                    log_key=log_key, connector_value=connector_value
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive guard
            logger.exception(
                "Failed to dispatch automatic metadata requests for charger %s",
                self.charger_id,
            )

    async def _request_configuration_snapshot(
        self, *, log_key: str, connector_value: int | None
    ) -> bool:
        """Send a GetConfiguration call for the current connection."""

        message_id = uuid.uuid4().hex
        frame = json.dumps([2, message_id, "GetConfiguration", {}])
        try:
            await self.send(frame)
        except Exception as exc:  # pragma: no cover - network error
            logger.warning(
                "Automatic GetConfiguration request for %s failed: %s",
                self.charger_id,
                exc,
            )
            return False

        store.add_log(log_key, f"< {frame}", log_type="charger")
        store.register_pending_call(
            message_id,
            {
                "action": "GetConfiguration",
                "charger_id": self.charger_id,
                "connector_id": connector_value,
                "log_key": log_key,
                "requested_at": timezone.now(),
            },
        )
        store.schedule_call_timeout(
            message_id,
            timeout=5.0,
            action="GetConfiguration",
            log_key=log_key,
            message=(
                "GetConfiguration timed out: charger did not respond"
                " (operation may not be supported)"
            ),
        )
        return True

    async def _request_firmware_snapshot(
        self, *, log_key: str, connector_value: int | None
    ) -> bool:
        """Send a firmware download request via DataTransfer."""

        charger = self.charger
        if not charger or not getattr(charger, "pk", None):
            return False

        def _has_pending_request() -> bool:
            return CPFirmwareRequest.objects.filter(
                charger=charger,
                responded_at__isnull=True,
            ).exists()

        pending = await database_sync_to_async(_has_pending_request)()
        if pending:
            return False

        vendor_setting = getattr(
            settings, "OCPP_AUTOMATIC_FIRMWARE_VENDOR_ID", DEFAULT_FIRMWARE_VENDOR_ID
        )
        vendor_id = str(vendor_setting or "").strip() or DEFAULT_FIRMWARE_VENDOR_ID
        message_id = uuid.uuid4().hex
        payload = {"vendorId": vendor_id, "messageId": "DownloadFirmware"}
        frame = json.dumps([2, message_id, "DataTransfer", payload])
        try:
            await self.send(frame)
        except Exception as exc:  # pragma: no cover - network error
            logger.warning(
                "Automatic firmware request for %s failed: %s",
                self.charger_id,
                exc,
            )
            return False

        def _create_message():
            message = DataTransferMessage.objects.create(
                charger=charger,
                connector_id=connector_value,
                direction=DataTransferMessage.DIRECTION_CSMS_TO_CP,
                ocpp_message_id=message_id,
                vendor_id=vendor_id,
                message_id="DownloadFirmware",
                payload=payload,
                status="Pending",
            )
            CPFirmwareRequest.objects.create(
                charger=charger,
                connector_id=connector_value,
                vendor_id=vendor_id,
                message=message,
            )
            return message

        message = await database_sync_to_async(_create_message)()
        if not message:
            return False

        store.add_log(
            log_key,
            "Requested firmware download via DataTransfer.",
            log_type="charger",
        )
        store.register_pending_call(
            message_id,
            {
                "action": "DataTransfer",
                "charger_id": self.charger_id,
                "connector_id": connector_value,
                "log_key": log_key,
                "message_pk": message.pk,
            },
        )
        store.schedule_call_timeout(
            message_id,
            action="DataTransfer",
            log_key=log_key,
        )
        return True

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

        await database_sync_to_async(_update_deployments)([target.pk for target in targets])

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
        configuration.save(update_fields=["raw_payload", "evcs_snapshot_at", "updated_at"])
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
        if action == "ChangeConfiguration":
            key_value = str(metadata.get("key") or "").strip()
            status_value = str(payload_data.get("status") or "").strip()
            stored_value = metadata.get("value")
            parts: list[str] = []
            if status_value:
                parts.append(f"status={status_value}")
            if key_value:
                parts.append(f"key={key_value}")
            if stored_value is not None:
                parts.append(f"value={stored_value}")
            message = "ChangeConfiguration result"
            if parts:
                message += ": " + ", ".join(parts)
            store.add_log(log_key, message, log_type="charger")
            if status_value.casefold() in {"accepted", "rebootrequired"} and key_value:
                connector_hint = metadata.get("connector_id")

                def _apply() -> ChargerConfiguration:
                    return self._apply_change_configuration_snapshot(
                        key_value,
                        stored_value if isinstance(stored_value, str) else None,
                        connector_hint,
                    )

                configuration = await database_sync_to_async(_apply)()
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
                message = (
                    DataTransferMessage.objects.select_related("firmware_request")
                    .filter(pk=message_pk)
                    .first()
                )
                if not message:
                    return
                status_value = (
                    str((payload or {}).get("status") or "").strip() or "Accepted"
                )
                timestamp = timezone.now()
                message.status = status_value
                message.response_data = (payload or {}).get("data")
                message.error_code = ""
                message.error_description = ""
                message.error_details = None
                message.responded_at = timestamp
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
                request = getattr(message, "firmware_request", None)
                if request:
                    request.status = status_value
                    request.responded_at = timestamp
                    request.response_payload = payload
                    request.save(
                        update_fields=[
                            "status",
                            "responded_at",
                            "response_payload",
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
        if action == "GetLog":
            request_pk = metadata.get("log_request_pk")
            capture_key = metadata.get("capture_key")
            status_value = str(payload_data.get("status") or "").strip()
            filename_value = str(
                payload_data.get("filename")
                or payload_data.get("location")
                or ""
            ).strip()
            location_value = str(payload_data.get("location") or "").strip()
            fragments: list[str] = []
            data_candidate = payload_data.get("logData") or payload_data.get("entries")
            if isinstance(data_candidate, (list, tuple)):
                for entry in data_candidate:
                    if entry is None:
                        continue
                    if isinstance(entry, (bytes, bytearray)):
                        try:
                            fragments.append(entry.decode("utf-8"))
                        except Exception:
                            fragments.append(base64.b64encode(entry).decode("ascii"))
                    else:
                        fragments.append(str(entry))
            elif data_candidate not in (None, ""):
                fragments.append(str(data_candidate))

            def _update_request() -> str:
                request = None
                if request_pk:
                    request = ChargerLogRequest.objects.filter(pk=request_pk).first()
                if request is None:
                    return ""
                updates: dict[str, object] = {
                    "responded_at": timezone.now(),
                    "raw_response": payload_data,
                }
                if status_value:
                    updates["status"] = status_value
                if filename_value:
                    updates["filename"] = filename_value
                if location_value:
                    updates["location"] = location_value
                if capture_key:
                    updates["session_key"] = str(capture_key)
                message_identifier = metadata.get("message_id")
                if message_identifier:
                    updates["message_id"] = str(message_identifier)
                ChargerLogRequest.objects.filter(pk=request.pk).update(**updates)
                for field, value in updates.items():
                    setattr(request, field, value)
                return request.session_key or ""

            session_capture = await database_sync_to_async(_update_request)()
            message = "GetLog result"
            if status_value:
                message += f": status={status_value}"
            if filename_value:
                message += f", filename={filename_value}"
            if location_value:
                message += f", location={location_value}"
            store.add_log(log_key, message, log_type="charger")
            if capture_key and fragments:
                for fragment in fragments:
                    store.append_log_capture(str(capture_key), fragment)
                store.finalize_log_capture(str(capture_key))
            elif session_capture and status_value.lower() in {
                "uploaded",
                "uploadfailure",
                "rejected",
                "idle",
            }:
                store.finalize_log_capture(session_capture)
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "SendLocalList":
            status_value = str(payload_data.get("status") or "").strip()
            version_candidate = (
                payload_data.get("currentLocalListVersion")
                or payload_data.get("listVersion")
                or metadata.get("list_version")
            )
            message = "SendLocalList result"
            if status_value:
                message += f": status={status_value}"
            if version_candidate is not None:
                message += f", version={version_candidate}"
            store.add_log(log_key, message, log_type="charger")
            version_int = None
            if version_candidate is not None:
                try:
                    version_int = int(version_candidate)
                except (TypeError, ValueError):
                    version_int = None
            await self._update_local_authorization_state(version_int)
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "GetLocalListVersion":
            version_candidate = payload_data.get("listVersion")
            processed = 0
            auth_list = payload_data.get("localAuthorizationList")
            if isinstance(auth_list, list):
                processed = await self._apply_local_authorization_entries(auth_list)
            message = "GetLocalListVersion result"
            if version_candidate is not None:
                message += f": version={version_candidate}"
            if processed:
                message += f", entries={processed}"
            store.add_log(log_key, message, log_type="charger")
            version_int = None
            if version_candidate is not None:
                try:
                    version_int = int(version_candidate)
                except (TypeError, ValueError):
                    version_int = None
            await self._update_local_authorization_state(version_int)
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "ClearCache":
            status_value = str(payload_data.get("status") or "").strip()
            message = "ClearCache result"
            if status_value:
                message += f": status={status_value}"
            store.add_log(log_key, message, log_type="charger")
            version_int = 0 if status_value == "Accepted" else None
            await self._update_local_authorization_state(version_int)
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "UpdateFirmware":
            deployment_pk = metadata.get("deployment_pk")

            def _apply():
                if not deployment_pk:
                    return
                deployment = CPFirmwareDeployment.objects.filter(
                    pk=deployment_pk
                ).first()
                if not deployment:
                    return
                status_value = str(payload_data.get("status") or "").strip() or "Accepted"
                deployment.mark_status(
                    status_value,
                    "",
                    timezone.now(),
                    response=payload_data,
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
        if action == "ReserveNow":
            status_value = str(payload_data.get("status") or "").strip()
            message = "ReserveNow result"
            if status_value:
                message += f": status={status_value}"
            store.add_log(log_key, message, log_type="charger")

            reservation_pk = metadata.get("reservation_pk")

            def _apply():
                if not reservation_pk:
                    return
                reservation = CPReservation.objects.filter(pk=reservation_pk).first()
                if not reservation:
                    return
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

            await database_sync_to_async(_apply)()
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                payload=payload_data,
            )
            return
        if action == "CancelReservation":
            status_value = str(payload_data.get("status") or "").strip()
            message = "CancelReservation result"
            if status_value:
                message += f": status={status_value}"
            store.add_log(log_key, message, log_type="charger")

            reservation_pk = metadata.get("reservation_pk")

            def _apply():
                if not reservation_pk:
                    return
                reservation = CPReservation.objects.filter(pk=reservation_pk).first()
                if not reservation:
                    return
                reservation.evcs_status = status_value
                reservation.evcs_error = ""
                reservation.evcs_confirmed = False
                reservation.evcs_confirmed_at = None
                reservation.save(
                    update_fields=[
                        "evcs_status",
                        "evcs_error",
                        "evcs_confirmed",
                        "evcs_confirmed_at",
                        "updated_on",
                    ]
                )

            await database_sync_to_async(_apply)()
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
        if action == "ChangeConfiguration":
            key_value = str(metadata.get("key") or "").strip()
            parts: list[str] = []
            if key_value:
                parts.append(f"key={key_value}")
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
            message = "ChangeConfiguration error"
            if parts:
                message += ": " + ", ".join(parts)
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
        if action == "GetLog":
            request_pk = metadata.get("log_request_pk")
            capture_key = metadata.get("capture_key")

            def _apply_error() -> None:
                if not request_pk:
                    return
                request = ChargerLogRequest.objects.filter(pk=request_pk).first()
                if not request:
                    return
                label = (error_code or "Error").strip() or "Error"
                request.status = label
                request.responded_at = timezone.now()
                request.raw_response = {
                    "errorCode": error_code,
                    "errorDescription": description,
                    "details": details,
                }
                if capture_key:
                    request.session_key = str(capture_key)
                request.save(
                    update_fields=[
                        "status",
                        "responded_at",
                        "raw_response",
                        "session_key",
                    ]
                )

            await database_sync_to_async(_apply_error)()
            parts: list[str] = []
            if error_code:
                parts.append(f"code={error_code}")
            if description:
                parts.append(f"description={description}")
            message = "GetLog error"
            if parts:
                message += ": " + ", ".join(parts)
            store.add_log(log_key, message, log_type="charger")
            if capture_key:
                store.finalize_log_capture(str(capture_key))
            store.record_pending_call_result(
                message_id,
                metadata=metadata,
                success=False,
                error_code=error_code,
                error_description=description,
                error_details=details,
            )
            return
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
                message = (
                    DataTransferMessage.objects.select_related("firmware_request")
                    .filter(pk=message_pk)
                    .first()
                )
                if not message:
                    return
                status_value = (error_code or "Error").strip() or "Error"
                timestamp = timezone.now()
                message.status = status_value
                message.response_data = None
                message.error_code = (error_code or "").strip()
                message.error_description = (description or "").strip()
                message.error_details = details
                message.responded_at = timestamp
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
                request = getattr(message, "firmware_request", None)
                if request:
                    request.status = status_value
                    request.responded_at = timestamp
                    request.response_payload = {
                        "errorCode": error_code,
                        "errorDescription": description,
                        "details": details,
                    }
                    request.save(
                        update_fields=[
                            "status",
                            "responded_at",
                            "response_payload",
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
        if action == "ClearCache":
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
            message = "ClearCache error"
            if parts:
                message += ": " + ", ".join(parts)
            store.add_log(log_key, message, log_type="charger")
            await self._update_local_authorization_state(None)
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
        if action == "UpdateFirmware":
            deployment_pk = metadata.get("deployment_pk")

            def _apply():
                if not deployment_pk:
                    return
                deployment = CPFirmwareDeployment.objects.filter(
                    pk=deployment_pk
                ).first()
                if not deployment:
                    return
                parts: list[str] = []
                if error_code:
                    parts.append(f"code={str(error_code).strip()}")
                if description:
                    parts.append(f"description={str(description).strip()}")
                if details:
                    try:
                        details_text = json.dumps(
                            details, sort_keys=True, ensure_ascii=False
                        )
                    except TypeError:
                        details_text = str(details)
                    if details_text:
                        parts.append(f"details={details_text}")
                message = "UpdateFirmware error"
                if parts:
                    message += ": " + ", ".join(parts)
                deployment.mark_status(
                    "Error",
                    message,
                    timezone.now(),
                    response=details or {},
                )
                deployment.completed_at = timezone.now()
                deployment.save(update_fields=["completed_at", "updated_at"])

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
        if action == "ReserveNow":
            parts: list[str] = []
            code_text = (error_code or "").strip() if error_code else ""
            if code_text:
                parts.append(f"code={code_text}")
            description_text = (description or "").strip() if description else ""
            if description_text:
                parts.append(f"description={description_text}")
            details_text = ""
            if details:
                try:
                    details_text = json.dumps(details, sort_keys=True, ensure_ascii=False)
                except TypeError:
                    details_text = str(details)
            if details_text:
                parts.append(f"details={details_text}")
            message = "ReserveNow error"
            if parts:
                message += ": " + ", ".join(parts)
            store.add_log(log_key, message, log_type="charger")

            reservation_pk = metadata.get("reservation_pk")

            def _apply():
                if not reservation_pk:
                    return
                reservation = CPReservation.objects.filter(pk=reservation_pk).first()
                if not reservation:
                    return
                summary_parts = []
                if code_text:
                    summary_parts.append(code_text)
                if description_text:
                    summary_parts.append(description_text)
                if details_text:
                    summary_parts.append(details_text)
                reservation.evcs_status = ""
                reservation.evcs_error = "; ".join(summary_parts)
                reservation.evcs_confirmed = False
                reservation.evcs_confirmed_at = None
                reservation.save(
                    update_fields=[
                        "evcs_status",
                        "evcs_error",
                        "evcs_confirmed",
                        "evcs_confirmed_at",
                        "updated_on",
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
        if action == "CancelReservation":
            parts: list[str] = []
            code_text = (error_code or "").strip() if error_code else ""
            if code_text:
                parts.append(f"code={code_text}")
            description_text = (description or "").strip() if description else ""
            if description_text:
                parts.append(f"description={description_text}")
            details_text = ""
            if details:
                try:
                    details_text = json.dumps(details, sort_keys=True, ensure_ascii=False)
                except TypeError:
                    details_text = str(details)
            if details_text:
                parts.append(f"details={details_text}")
            message = "CancelReservation error"
            if parts:
                message += ": " + ", ".join(parts)
            store.add_log(log_key, message, log_type="charger")

            reservation_pk = metadata.get("reservation_pk")

            def _apply():
                if not reservation_pk:
                    return
                reservation = CPReservation.objects.filter(pk=reservation_pk).first()
                if not reservation:
                    return
                summary_parts = []
                if code_text:
                    summary_parts.append(code_text)
                if description_text:
                    summary_parts.append(description_text)
                if details_text:
                    summary_parts.append(details_text)
                reservation.evcs_status = ""
                reservation.evcs_error = "; ".join(summary_parts)
                reservation.evcs_confirmed = False
                reservation.evcs_confirmed_at = None
                reservation.save(
                    update_fields=[
                        "evcs_status",
                        "evcs_error",
                        "evcs_confirmed",
                        "evcs_confirmed_at",
                        "updated_on",
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
                and not any(target.pk == aggregate.pk for target in targets if target.pk)
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
        metadata_task = getattr(self, "_initial_metadata_task", None)
        if metadata_task is not None:
            metadata_task.cancel()
            try:
                await metadata_task
            except asyncio.CancelledError:
                pass
            self._initial_metadata_task = None
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
                    connector_letter = Charger.connector_letter_from_slug(
                        connector_slug_value
                    )
                    if connector_letter:
                        suffix = f" (connector {connector_letter})"
                    else:
                        suffix = f" (connector {connector_slug_value})"
                store.add_log(
                    follow_up_log_key,
                    f"TriggerMessage follow-up received: {target_label}{suffix}",
                    log_type="charger",
                )
            await self._assign_connector(payload.get("connectorId"))
            await self._forward_charge_point_message(action, raw)
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
            elif action == "SecurityEventNotification":
                event_type = str(
                    payload.get("type")
                    or payload.get("eventType")
                    or ""
                ).strip()
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
            elif action == "LogStatusNotification":
                status_value = str(payload.get("status") or "").strip()
                log_type_value = str(payload.get("logType") or "").strip()
                request_identifier = payload.get("requestId")
                timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
                if timestamp_value is None:
                    timestamp_value = timezone.now()
                location_value = str(
                    payload.get("location")
                    or payload.get("remoteLocation")
                    or ""
                ).strip()
                filename_value = str(payload.get("filename") or "").strip()

                def _persist_log_status() -> str:
                    qs = ChargerLogRequest.objects.filter(
                        charger__charger_id=self.charger_id
                    )
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
                    await self._record_rfid_attempt(
                        rfid=id_tag or "",
                        status=RFIDSessionAttempt.Status.ACCEPTED,
                        account=account,
                        transaction=tx_obj,
                    )
                    reply_payload = {
                        "transactionId": tx_obj.pk,
                        "idTagInfo": {"status": "Accepted"},
                    }
                else:
                    reply_payload = {"idTagInfo": {"status": "Invalid"}}
                    await self._record_rfid_attempt(
                        rfid=id_tag or "",
                        status=RFIDSessionAttempt.Status.REJECTED,
                        account=account,
                    )
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
