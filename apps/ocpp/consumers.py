import base64
import binascii
import ipaddress
import re
from datetime import datetime, timezone as dt_timezone
import asyncio
from collections import deque
import inspect
import json
import logging
import uuid
from urllib.parse import parse_qs
from django.conf import settings
from django.utils import timezone
from apps.energy.models import CustomerAccount
from apps.links.models import Reference
from apps.cards.models import RFID as CoreRFID
from apps.nodes.models import NetMessage
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel
from django.core.exceptions import ValidationError
from django.contrib.auth import authenticate

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from apps.rates.mixins import RateLimitedConsumerMixin
from config.offline import requires_network

from . import store
from .forwarder import forwarder
from .status_resets import STATUS_RESET_UPDATES, clear_cached_statuses
from .call_error_handlers import dispatch_call_error
from .call_result_handlers import dispatch_call_result
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
    PowerProjection,
)
from apps.links.reference_utils import host_is_local_loopback
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


class CSMSConsumer(RateLimitedConsumerMixin, AsyncWebsocketConsumer):
    """Very small subset of OCPP 1.6 CSMS behaviour."""

    consumption_update_interval = 300
    rate_limit_target = Charger
    rate_limit_scope = "ocpp-connect"
    rate_limit_fallback = store.MAX_CONNECTIONS_PER_IP
    rate_limit_window = 60

    def _client_ip_is_local(self) -> bool:
        parsed = _parse_ip(getattr(self, "client_ip", None))
        if not parsed:
            return False
        return parsed.is_private or parsed.is_loopback or parsed.is_link_local

    def get_rate_limit_identifier(self) -> str | None:
        if self._client_ip_is_local():
            return None
        return super().get_rate_limit_identifier()

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

    def _parse_basic_auth_header(self) -> tuple[tuple[str, str] | None, str | None]:
        """Return decoded Basic auth credentials and an error code if any."""

        headers = self.scope.get("headers") or []
        for raw_name, raw_value in headers:
            if not isinstance(raw_name, (bytes, bytearray)):
                continue
            if raw_name.lower() != b"authorization":
                continue
            try:
                header_value = raw_value.decode("latin1")
            except Exception:
                return None, "invalid"
            scheme, _, param = header_value.partition(" ")
            if scheme.lower() != "basic" or not param:
                return None, "invalid"
            try:
                decoded = base64.b64decode(param.strip(), validate=True).decode(
                    "utf-8"
                )
            except (binascii.Error, UnicodeDecodeError):
                return None, "invalid"
            username, sep, password = decoded.partition(":")
            if not sep:
                return None, "invalid"
            return (username, password), None
        return None, "missing"

    async def _authenticate_basic_credentials(
        self, username: str, password: str
    ):
        """Return the authenticated user for HTTP Basic credentials, if valid."""

        if username is None or password is None:
            return None

        user = await sync_to_async(authenticate)(
            request=None, username=username, password=password
        )
        if user is None or not getattr(user, "is_active", False):
            return None
        return user

    def _select_subprotocol(
        self, offered: list[str] | tuple[str, ...], preferred: str | None
    ) -> str | None:
        """Choose the negotiated OCPP subprotocol, honoring stored preference."""

        available = [proto for proto in offered if proto]
        preferred_normalized = (preferred or "").strip()
        if preferred_normalized and preferred_normalized in available:
            return preferred_normalized
        # Prefer the latest OCPP 2.0.1 protocol when the charger requests it,
        # otherwise fall back to older versions.
        if "ocpp2.0.1" in available:
            return "ocpp2.0.1"
        if "ocpp2.0" in available:
            return "ocpp2.0"
        # Operational safeguard: never reject a charger solely because it omits
        # or sends an unexpected subprotocol.  We negotiate ``ocpp1.6`` when the
        # charger offers it, but otherwise continue without a subprotocol so we
        # accept as many real-world stations as possible.
        if "ocpp1.6" in available:
            return "ocpp1.6"
        return None

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
        self.client_ip = _resolve_client_ip(self.scope)
        self._header_reference_created = False
        existing_charger = await database_sync_to_async(
            lambda: Charger.objects.select_related(
                "ws_auth_user", "ws_auth_group", "station_model"
            )
            .filter(charger_id=self.charger_id, connector_id=None)
            .first(),
            thread_sensitive=False,
        )()
        preferred_version = (
            existing_charger.preferred_ocpp_version_value()
            if existing_charger
            else ""
        )
        offered = self.scope.get("subprotocols", [])
        subprotocol = self._select_subprotocol(offered, preferred_version)
        self.preferred_ocpp_version = preferred_version
        negotiated_version = subprotocol
        if not negotiated_version and preferred_version in {"ocpp2.0", "ocpp2.0.1"}:
            negotiated_version = preferred_version
        self.ocpp_version = negotiated_version or "ocpp1.6"
        if existing_charger and existing_charger.requires_ws_auth:
            credentials, error_code = self._parse_basic_auth_header()
            rejection_reason: str | None = None
            if error_code == "missing":
                rejection_reason = "HTTP Basic authentication required (credentials missing)"
            elif error_code == "invalid":
                rejection_reason = "HTTP Basic authentication header is invalid"
            else:
                if not credentials:
                    rejection_reason = "HTTP Basic authentication header is invalid"
                else:
                    username, password = credentials
                    auth_user = await self._authenticate_basic_credentials(
                        username, password
                    )
                    if auth_user is None:
                        rejection_reason = "HTTP Basic authentication failed"
                    else:
                        authorized = await database_sync_to_async(
                            existing_charger.is_ws_user_authorized
                        )(auth_user)
                        if not authorized:
                            user_label = getattr(auth_user, "get_username", None)
                            if callable(user_label):
                                user_label = user_label()
                            else:
                                user_label = getattr(auth_user, "username", "")
                            if user_label:
                                rejection_reason = (
                                    "HTTP Basic authentication rejected for unauthorized user "
                                    f"'{user_label}'"
                                )
                            else:
                                rejection_reason = (
                                    "HTTP Basic authentication rejected for unauthorized user"
                                )
            if rejection_reason:
                store.add_log(
                    self.store_key,
                    f"Rejected connection: {rejection_reason}",
                    log_type="charger",
                )
                await self.close(code=4003)
                return
        # Close any pending connection for this charger so reconnections do
        # not leak stale consumers when the connector id has not been
        # negotiated yet.
        existing = store.connections.get(self.store_key)
        if existing is not None:
            store.release_ip_connection(getattr(existing, "client_ip", None), existing)
            await existing.close()
        if not await self.enforce_rate_limit():
            store.add_log(
                self.store_key,
                f"Rejected connection from {self.client_ip or 'unknown'}: rate limit exceeded",
                log_type="charger",
            )
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
        created = False
        if existing_charger is not None:
            self.charger = existing_charger
        else:
            self.charger, created = await database_sync_to_async(
                Charger.objects.get_or_create
            )(
                charger_id=self.charger_id,
                connector_id=None,
                defaults={"last_path": self.scope.get("path", "")},
            )
        await database_sync_to_async(self.charger.refresh_manager_node)()
        self.aggregate_charger = self.charger
        await self._clear_cached_status_fields()
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

        restored_calls = store.restore_pending_calls(self.charger_id)
        if restored_calls:
            store.add_log(
                self.store_key,
                f"Restored {len(restored_calls)} pending call(s) after reconnect",
                log_type="charger",
            )

        if not created:
            await database_sync_to_async(
                forwarder.sync_forwarded_charge_points
            )(refresh_forwarders=False)

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

    async def _clear_cached_status_fields(self) -> None:
        """Clear stale status fields for this charger across all connectors."""

        def _clear_for_charger():
            return clear_cached_statuses([self.charger_id])

        cleared = await database_sync_to_async(
            _clear_for_charger, thread_sensitive=False
        )()
        if not cleared:
            return

        targets = {self.charger, self.aggregate_charger}
        for target in [t for t in targets if t is not None]:
            for field, value in STATUS_RESET_UPDATES.items():
                setattr(target, field, value)

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
        await database_sync_to_async(tx_obj.save)(
            update_fields=["ocpp_transaction_id"]
        )

    async def _ensure_forwarding_context(
        self, charger,
    ) -> tuple[tuple[str, ...], int | None] | None:
        """Return forwarding configuration for ``charger`` when available."""

        if not charger or not getattr(charger, "forwarded_to_id", None):
            return None

        def _resolve():
            from apps.ocpp.models import CPForwarder

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
                from apps.ocpp.models import CPForwarder

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
        session = forwarder.get_session(charger.pk)
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
            forwarder.remove_session(charger.pk)
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
                    pk=tx_id,
                    charger=self.charger,
                    start_time=timezone.now(),
                    ocpp_transaction_id=str(tx_id),
                )
                store.start_session_log(self.store_key, tx_obj.pk)
                store.add_session_message(self.store_key, raw_message)
            store.transactions[self.store_key] = tx_obj
        else:
            tx_obj = store.transactions.get(self.store_key)

        await self._ensure_ocpp_transaction_identifier(tx_obj, str(tx_id) if tx_id else None)
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
            tx = (
                Transaction.objects.select_related("charger")
                .filter(pk=tx_id)
                .first()
            )
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
                    getattr(charger, "charger_id", "")
                    or self.charger_id
                    or ""
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
            if existing_uuid:
                msg = NetMessage.objects.filter(uuid=existing_uuid).first()
                if msg:
                    msg.subject = subject_value
                    msg.body = body_value
                    msg.save(update_fields=["subject", "body"])
                    msg.propagate()
                    return str(msg.uuid)
            msg = NetMessage.broadcast(subject=subject_value, body=body_value)
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
        handled = await dispatch_call_result(
            self,
            action,
            message_id,
            metadata,
            payload_data,
            log_key,
        )
        if handled:
            return
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
        handled = await dispatch_call_error(
            self,
            action,
            message_id,
            metadata,
            error_code,
            description,
            details,
            log_key,
        )
        if handled:
            return
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
        store.connections.pop(self.store_key, None)
        pending_key = store.pending_key(self.charger_id)
        if self.store_key != pending_key:
            store.connections.pop(pending_key, None)
        store.end_session_log(self.store_key)
        store.stop_session_lock()
        store.clear_pending_calls(self.charger_id)
        store.add_log(self.store_key, f"Closed (code={close_code})", log_type="charger")

    async def receive(self, text_data=None, bytes_data=None):
        raw = self._normalize_raw_message(text_data, bytes_data)
        if raw is None:
            return
        store.add_log(self.store_key, raw, log_type="charger")
        store.add_session_message(self.store_key, raw)
        msg = self._parse_message(raw)
        if msg is None:
            return
        message_type = msg[0]
        if message_type == 2:
            await self._handle_call_message(msg, raw, text_data)
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

    def _normalize_raw_message(self, text_data, bytes_data):
        raw = text_data
        if raw is None and bytes_data is not None:
            raw = base64.b64encode(bytes_data).decode("ascii")
        return raw

    def _parse_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(msg, list) or not msg:
            return None
        return msg

    async def _handle_call_message(self, msg, raw, text_data):
        msg_id, action = msg[1], msg[2]
        payload = msg[3] if len(msg) > 3 else {}
        connector_hint = payload.get("connectorId") if isinstance(payload, dict) else None
        self._log_triggered_follow_up(action, connector_hint)
        await self._assign_connector(payload.get("connectorId"))
        await self._forward_charge_point_message(action, raw)
        action_handlers = {
            "BootNotification": self._handle_boot_notification_action,
            "DataTransfer": self._handle_data_transfer_action,
            "Heartbeat": self._handle_heartbeat_action,
            "StatusNotification": self._handle_status_notification_action,
            "Authorize": self._handle_authorize_action,
            "MeterValues": self._handle_meter_values_action,
            "TransactionEvent": self._handle_transaction_event_action,
            "SecurityEventNotification": self._handle_security_event_notification_action,
            "DiagnosticsStatusNotification": self._handle_diagnostics_status_notification_action,
            "LogStatusNotification": self._handle_log_status_notification_action,
            "StartTransaction": self._handle_start_transaction_action,
            "StopTransaction": self._handle_stop_transaction_action,
            "FirmwareStatusNotification": self._handle_firmware_status_notification_action,
        }
        reply_payload = {}
        handler = action_handlers.get(action)
        if handler:
            reply_payload = await handler(payload, msg_id, raw, text_data)
        response = [3, msg_id, reply_payload]
        await self.send(json.dumps(response))
        store.add_log(
            self.store_key, f"< {json.dumps(response)}", log_type="charger"
        )

    def _log_triggered_follow_up(self, action: str, connector_hint):
        follow_up = store.consume_triggered_followup(
            self.charger_id, action, connector_hint
        )
        if not follow_up:
            return
        follow_up_log_key = follow_up.get("log_key") or self.store_key
        target_label = follow_up.get("target") or action
        connector_slug_value = follow_up.get("connector")
        suffix = ""
        if connector_slug_value and connector_slug_value != store.AGGREGATE_SLUG:
            connector_letter = Charger.connector_letter_from_slug(connector_slug_value)
            if connector_letter:
                suffix = f" (connector {connector_letter})"
            else:
                suffix = f" (connector {connector_slug_value})"
        store.add_log(
            follow_up_log_key,
            f"TriggerMessage follow-up received: {target_label}{suffix}",
            log_type="charger",
        )

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "BootNotification")
    async def _handle_boot_notification_action(self, payload, msg_id, raw, text_data):
        current_time = datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "currentTime": current_time,
            "interval": 300,
            "status": "Accepted",
        }

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "DataTransfer")
    async def _handle_data_transfer_action(self, payload, msg_id, raw, text_data):
        return await self._handle_data_transfer(msg_id, payload)

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    async def _handle_heartbeat_action(self, payload, msg_id, raw, text_data):
        current_time = datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z")
        reply_payload = {"currentTime": current_time}
        now = timezone.now()
        self.charger.last_heartbeat = now
        if self.aggregate_charger and self.aggregate_charger is not self.charger:
            self.aggregate_charger.last_heartbeat = now
        await database_sync_to_async(
            Charger.objects.filter(charger_id=self.charger_id).update
        )(last_heartbeat=now)
        return reply_payload

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    async def _handle_status_notification_action(
        self, payload, msg_id, raw, text_data
    ):
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
        status_timestamp = parse_datetime(timestamp_raw) if timestamp_raw else None
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
        connector_value = payload.get("connectorId")

        def _update_status():
            target = None
            if self.aggregate_charger:
                target = self.aggregate_charger
            if connector_value is not None:
                target = Charger.objects.filter(
                    charger_id=self.charger_id,
                    connector_id=connector_value,
                ).first()
            if not target and not self.charger.connector_id:
                target = self.charger
            if target:
                for field, value in update_kwargs.items():
                    setattr(target, field, value)
                if target.pk:
                    Charger.objects.filter(pk=target.pk).update(**update_kwargs)
            connector = (
                Charger.objects.filter(
                    charger_id=self.charger_id,
                    connector_id=payload.get("connectorId"),
                )
                .exclude(pk=self.charger.pk)
                .first()
            )
            if connector:
                connector.last_status = status
                connector.last_error_code = error_code
                connector.last_status_vendor_info = vendor_value
                connector.last_status_timestamp = status_timestamp
                connector.save(update_fields=update_kwargs.keys())

        await database_sync_to_async(_update_status)()
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
        return {}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "Authorize")
    async def _handle_authorize_action(self, payload, msg_id, raw, text_data):
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
            elif id_tag and tag and not tag_created and tag.allowed:
                status = "Accepted"
                self._log_unlinked_rfid(tag.rfid)
        else:
            await self._ensure_rfid_seen(id_tag)
            status = "Accepted"
        return {"idTagInfo": {"status": status}}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    async def _handle_meter_values_action(self, payload, msg_id, raw, text_data):
        await self._store_meter_values(payload, text_data)
        self.charger.last_meter_values = payload
        await database_sync_to_async(
            Charger.objects.filter(pk=self.charger.pk).update
        )(last_meter_values=payload)
        return {}

    async def _handle_security_event_notification_action(
        self, payload, msg_id, raw, text_data
    ):
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
        return {}

    @protocol_call(
        "ocpp16",
        ProtocolCallModel.CP_TO_CSMS,
        "DiagnosticsStatusNotification",
    )
    async def _handle_diagnostics_status_notification_action(
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
            if (
                aggregate
                and not any(target.pk == aggregate.pk for target in targets if target.pk)
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
        return {}

    async def _handle_log_status_notification_action(
        self, payload, msg_id, raw, text_data
    ):
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
        return {}

    async def _handle_transaction_event_action(
        self, payload, msg_id, raw, text_data
    ):
        event_type = str(payload.get("eventType") or "").strip().lower()
        transaction_info = payload.get("transactionInfo") or {}
        ocpp_tx_id = str(transaction_info.get("transactionId") or "").strip()
        evse_info = payload.get("evse") or {}
        connector_hint = evse_info.get("connectorId", evse_info.get("id"))
        await self._assign_connector(connector_hint)
        connector_value = self.connector_value
        timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
        if timestamp_value is None:
            timestamp_value = timezone.now()

        id_token = payload.get("idToken") or {}
        id_tag = ""
        if isinstance(id_token, dict):
            id_tag = str(id_token.get("idToken") or "").strip()

        if event_type == "started":
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
                vid_value, vin_value = _extract_vehicle_identifier(payload)
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
                await self._process_meter_value_entries(
                    payload.get("meterValue"), connector_value, tx_obj
                )
                await self._record_rfid_attempt(
                    rfid=id_tag or "",
                    status=RFIDSessionAttempt.Status.ACCEPTED,
                    account=account,
                    transaction=tx_obj,
                )
                return {"idTokenInfo": {"status": "Accepted"}}

            await self._record_rfid_attempt(
                rfid=id_tag or "",
                status=RFIDSessionAttempt.Status.REJECTED,
                account=account,
            )
            return {"idTokenInfo": {"status": "Invalid"}}

        if event_type == "ended":
            tx_obj = store.transactions.pop(self.store_key, None)
            if not tx_obj and ocpp_tx_id:
                tx_obj = await Transaction.aget_by_ocpp_id(self.charger, ocpp_tx_id)
            if not tx_obj and ocpp_tx_id.isdigit():
                tx_obj = await database_sync_to_async(
                    Transaction.objects.filter(
                        pk=int(ocpp_tx_id), charger=self.charger
                    ).first
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
            vid_value, vin_value = _extract_vehicle_identifier(payload)
            if vid_value:
                tx_obj.vid = vid_value
            if vin_value:
                tx_obj.vin = vin_value
            await database_sync_to_async(tx_obj.save)()
            await self._process_meter_value_entries(
                payload.get("meterValue"), connector_value, tx_obj
            )
            await self._update_consumption_message(tx_obj.pk)
            await self._cancel_consumption_message()
            store.end_session_log(self.store_key)
            store.stop_session_lock()
            return {}

        if event_type == "updated":
            tx_obj = store.transactions.get(self.store_key)
            if not tx_obj and ocpp_tx_id:
                tx_obj = await Transaction.aget_by_ocpp_id(self.charger, ocpp_tx_id)
            if not tx_obj and ocpp_tx_id.isdigit():
                tx_obj = await database_sync_to_async(
                    Transaction.objects.filter(
                        pk=int(ocpp_tx_id), charger=self.charger
                    ).first
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
            await self._process_meter_value_entries(
                payload.get("meterValue"), connector_value, tx_obj
            )
            return {}

        return {}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StartTransaction")
    async def _handle_start_transaction_action(
        self, payload, msg_id, raw, text_data
    ):
        id_tag = payload.get("idTag")
        tag = None
        tag_created = False
        if id_tag:
            tag, tag_created = await database_sync_to_async(CoreRFID.register_scan)(
                id_tag
            )
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
                status=RFIDSessionAttempt.Status.ACCEPTED,
                account=account,
                transaction=tx_obj,
            )
            return {
                "transactionId": tx_obj.pk,
                "idTagInfo": {"status": "Accepted"},
            }
        await self._record_rfid_attempt(
            rfid=id_tag or "",
            status=RFIDSessionAttempt.Status.REJECTED,
            account=account,
        )
        return {"idTagInfo": {"status": "Invalid"}}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StopTransaction")
    async def _handle_stop_transaction_action(
        self, payload, msg_id, raw, text_data
    ):
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
                meter_start=payload.get("meterStart") or payload.get("meterStop"),
                vid=vid_value,
                vin=vin_value,
            )
        if tx_obj:
            await self._ensure_ocpp_transaction_identifier(tx_obj, str(tx_id))
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
        store.end_session_log(self.store_key)
        store.stop_session_lock()
        return {"idTagInfo": {"status": "Accepted"}}

    @protocol_call(
        "ocpp16",
        ProtocolCallModel.CP_TO_CSMS,
        "FirmwareStatusNotification",
    )
    async def _handle_firmware_status_notification_action(
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
            "FirmwareStatusNotification: "
            + json.dumps(payload, separators=(",", ":")),
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

