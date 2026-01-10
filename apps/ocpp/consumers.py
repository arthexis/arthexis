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
from apps.rates.models import RateLimit
from config.offline import requires_network

from . import store
from .forwarder import forwarder
from .status_resets import STATUS_RESET_UPDATES, clear_cached_statuses
from .call_error_handlers import dispatch_call_error
from .call_result_handlers import dispatch_call_result
from decimal import Decimal, InvalidOperation
from django.utils.dateparse import parse_datetime
from .models import (
    Transaction,
    Charger,
    ChargerConfiguration,
    MeterValue,
    CostUpdate,
    DataTransferMessage,
    CPReservation,
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    RFIDSessionAttempt,
    SecurityEvent,
    ChargerLogRequest,
    PowerProjection,
    ChargingProfile,
    CertificateRequest,
    CertificateOperation,
    CertificateStatusCheck,
    InstalledCertificate,
    Variable,
    MonitoringRule,
    MonitoringReport,
    DeviceInventorySnapshot,
    DeviceInventoryItem,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
    ClearedChargingLimitEvent,
)
from .services import certificate_signing
from apps.links.reference_utils import host_is_local_loopback
from .evcs_discovery import (
    DEFAULT_CONSOLE_PORT,
    HTTPS_PORTS,
    build_console_url,
    prioritise_ports,
    scan_open_ports,
)
from .utils import _parse_ocpp_timestamp

FORWARDED_PAIR_RE = re.compile(r"for=(?:\"?)(?P<value>[^;,\"\s]+)(?:\"?)", re.IGNORECASE)


logger = logging.getLogger(__name__)


OCPP_VERSION_16 = "ocpp1.6"
OCPP_VERSION_201 = "ocpp2.0.1"
OCPP_VERSION_21 = "ocpp2.1"
OCPP_CONNECT_RATE_LIMIT_FALLBACK = 1
OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS = 2


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


def _extract_vehicle_identifier(payload: dict) -> tuple[str, str]:
    """Return normalized VID and VIN values from an OCPP message payload."""

    raw_vid = payload.get("vid")
    vid_value = str(raw_vid).strip() if raw_vid is not None else ""
    raw_vin = payload.get("vin")
    vin_value = str(raw_vin).strip() if raw_vin is not None else ""
    if not vid_value and vin_value:
        vid_value = vin_value
    return vid_value, vin_value


def _register_log_names_for_identity(
    charger_id: str, connector_id: int | str | None, display_name: str
) -> None:
    """Register friendly log names for a charger identity and its pending key."""

    if not charger_id:
        return
    friendly_name = display_name or charger_id
    store.register_log_name(
        store.identity_key(charger_id, connector_id),
        friendly_name,
        log_type="charger",
    )
    if connector_id is None:
        store.register_log_name(
            store.pending_key(charger_id), friendly_name, log_type="charger"
        )
        store.register_log_name(charger_id, friendly_name, log_type="charger")


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
    rate_limit_fallback = OCPP_CONNECT_RATE_LIMIT_FALLBACK
    rate_limit_window = OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS

    def _client_ip_is_local(self) -> bool:
        parsed = _parse_ip(getattr(self, "client_ip", None))
        if not parsed:
            return False
        return parsed.is_private or parsed.is_loopback or parsed.is_link_local

    def get_rate_limit_identifier(self) -> str | None:
        if self._client_ip_is_local():
            return None
        return super().get_rate_limit_identifier()

    def _resolve_certificate_target(self) -> Charger | None:
        target = self.aggregate_charger or self.charger
        if target and target.pk:
            found = Charger.objects.filter(pk=target.pk).first()
            if found:
                return found

        charger_id = ""
        if target and getattr(target, "charger_id", ""):
            charger_id = target.charger_id
        elif getattr(self, "charger_id", ""):
            charger_id = self.charger_id

        if charger_id:
            found = Charger.objects.filter(charger_id=charger_id).first()
            if found:
                return found
            return Charger.objects.create(charger_id=charger_id)

        return None

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
                            self.serial_source = "query"
                            return trimmed

        serial = self.scope["url_route"]["kwargs"].get("cid", "").strip()
        if serial:
            self.serial_source = "route"
            return serial

        path = (self.scope.get("path") or "").strip("/")
        if not path:
            return ""

        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return ""

        serial = segments[-1].strip()
        if not serial:
            return ""
        self.serial_source = "path"
        return serial

    def _parse_basic_auth_header(self) -> tuple[tuple[str, str] | None, str | None]:
        """Return decoded Basic auth credentials and an error code if any."""

        headers = self.scope.get("headers") or []
        for raw_name, raw_value in headers:
            if not isinstance(raw_name, (bytes, bytearray)):
                continue
            if raw_name.lower() != b"authorization":
                continue
            if not isinstance(raw_value, (bytes, bytearray)):
                return None, "invalid"
            try:
                header_value = raw_value.decode("latin1")
            except UnicodeDecodeError:
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

        available: list[str] = []
        for proto in offered:
            if not proto:
                continue
            if isinstance(proto, bytes):
                try:
                    proto_text = proto.decode("latin1")
                except Exception:
                    continue
            else:
                proto_text = str(proto)
            proto_text = proto_text.strip()
            if proto_text:
                available.append(proto_text)
        preferred_normalized = (preferred or "").strip()
        if preferred_normalized and preferred_normalized in available:
            return preferred_normalized
        # Prefer the latest supported OCPP 2.x protocol when the charger
        # requests it, otherwise fall back to older versions.
        if OCPP_VERSION_21 in available:
            return OCPP_VERSION_21
        if OCPP_VERSION_201 in available:
            return OCPP_VERSION_201
        # Operational safeguard: never reject a charger solely because it omits
        # or sends an unexpected subprotocol.  We negotiate ``ocpp1.6`` when the
        # charger offers it, but otherwise continue without a subprotocol so we
        # accept as many real-world stations as possible.
        if OCPP_VERSION_16 in available:
            return OCPP_VERSION_16
        return None

    def _get_offered_subprotocols(self) -> list[str]:
        """Return the subprotocols offered by the connecting websocket client."""

        offered = self.scope.get("subprotocols") or []
        normalized: list[str] = []
        for proto in offered:
            try:
                if isinstance(proto, (bytes, bytearray)):
                    value = proto.decode("latin1")
                else:
                    value = str(proto)
            except (AttributeError, TypeError, UnicodeDecodeError):
                continue
            value = value.strip()
            if value:
                normalized.append(value)
        if normalized:
            return normalized

        headers = self.scope.get("headers") or []
        for raw_name, raw_value in headers:
            if not isinstance(raw_name, (bytes, bytearray)):
                continue
            if raw_name.lower() != b"sec-websocket-protocol":
                continue
            try:
                header_value = raw_value.decode("latin1")
                for candidate in header_value.split(","):
                    trimmed = candidate.strip()
                    if trimmed:
                        normalized.append(trimmed)
            except (AttributeError, TypeError, UnicodeDecodeError):
                continue
        return normalized

    async def _validate_serial_or_reject(self, raw_serial: str) -> bool:
        """Validate the charge point serial and reject the connection if invalid."""

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
            return False
        return True

    def _negotiate_ocpp_version(self, existing_charger: Charger | None) -> str | None:
        """Resolve the negotiated OCPP subprotocol and set version attributes."""

        preferred_version = (
            existing_charger.preferred_ocpp_version_value()
            if existing_charger
            else ""
        )
        offered = self._get_offered_subprotocols()
        subprotocol = self._select_subprotocol(offered, preferred_version)
        self.preferred_ocpp_version = preferred_version
        negotiated_version = subprotocol
        if not negotiated_version and preferred_version in {
            OCPP_VERSION_201,
            OCPP_VERSION_21,
        }:
            negotiated_version = preferred_version
        self.ocpp_version = negotiated_version or OCPP_VERSION_16
        return subprotocol

    async def _enforce_ws_auth(self, existing_charger: Charger | None) -> bool:
        """Enforce HTTP Basic auth requirements for websocket connections."""

        if not existing_charger or not existing_charger.requires_ws_auth:
            return True
        credentials, error_code = self._parse_basic_auth_header()
        rejection_reason: str | None = None
        if error_code == "missing":
            rejection_reason = "HTTP Basic authentication required (credentials missing)"
        elif error_code == "invalid":
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
            return False
        return True

    async def _accept_connection(self, subprotocol: str | None) -> bool:
        """Accept the websocket connection after rate limits are enforced."""

        existing = store.connections.get(self.store_key)
        replacing_existing = existing is not None
        if existing is not None:
            store.release_ip_connection(getattr(existing, "client_ip", None), existing)
            await existing.close()
        should_enforce_rate_limit = True
        if replacing_existing and getattr(existing, "client_ip", None) == self.client_ip:
            should_enforce_rate_limit = await self._has_rate_limit_rule()
        if should_enforce_rate_limit and not await self.enforce_rate_limit():
            store.add_log(
                self.store_key,
                f"Rejected connection from {self.client_ip or 'unknown'}: rate limit exceeded",
                log_type="charger",
            )
            return False
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
        return True

    async def _has_rate_limit_rule(self) -> bool:
        def _resolve_rule() -> bool:
            return (
                RateLimit.for_target(
                    self.get_rate_limit_target(), scope_key=self.rate_limit_scope
                )
                is not None
            )

        return await database_sync_to_async(_resolve_rule)()

    async def _ensure_charger_record(
        self, existing_charger: Charger | None
    ) -> bool:
        """Ensure a charger record exists and refresh cached metadata."""

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
        return created

    async def _register_charger_logs(self) -> None:
        """Register charger log names based on location or charger id."""

        location_name = await sync_to_async(
            lambda: self.charger.location.name if self.charger.location else ""
        )()
        friendly_name = location_name or self.charger_id
        _register_log_names_for_identity(self.charger_id, None, friendly_name)

    @requires_network
    async def connect(self):
        raw_serial = self._extract_serial_identifier()
        if not await self._validate_serial_or_reject(raw_serial):
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
        subprotocol = self._negotiate_ocpp_version(existing_charger)
        if not await self._enforce_ws_auth(existing_charger):
            return
        if not await self._accept_connection(subprotocol):
            return
        created = await self._ensure_charger_record(existing_charger)
        await self._register_charger_logs()

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
            _register_log_names_for_identity(self.charger_id, None, friendly_name)
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
        _register_log_names_for_identity(
            self.charger_id, connector_value, connector_name
        )
        aggregate_name = ""
        if self.aggregate_charger:
            aggregate_name = await sync_to_async(
                lambda: self.aggregate_charger.name or self.aggregate_charger.charger_id
            )()
        _register_log_names_for_identity(
            self.charger_id, None, aggregate_name or self.charger_id
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
        metadata_charger = metadata.get("charger_id")
        if metadata_charger and self.charger_id:
            metadata_serial = Charger.normalize_serial(str(metadata_charger)).casefold()
            consumer_serial = Charger.normalize_serial(self.charger_id).casefold()
            if metadata_serial and consumer_serial and metadata_serial != consumer_serial:
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
        metadata_charger = metadata.get("charger_id")
        if metadata_charger and self.charger_id:
            metadata_serial = Charger.normalize_serial(str(metadata_charger)).casefold()
            consumer_serial = Charger.normalize_serial(self.charger_id).casefold()
            if metadata_serial and consumer_serial and metadata_serial != consumer_serial:
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
        action_handlers = {
            "BootNotification": self._handle_boot_notification_action,
            "DataTransfer": self._handle_data_transfer_action,
            "Heartbeat": self._handle_heartbeat_action,
            "StatusNotification": self._handle_status_notification_action,
            "Authorize": self._handle_authorize_action,
            "MeterValues": self._handle_meter_values_action,
            "TransactionEvent": self._handle_transaction_event_action,
            "SecurityEventNotification": self._handle_security_event_notification_action,
            "NotifyChargingLimit": self._handle_notify_charging_limit_action,
            "ClearedChargingLimit": self._handle_cleared_charging_limit_action,
            "NotifyCustomerInformation": self._handle_notify_customer_information_action,
            "NotifyDisplayMessages": self._handle_notify_display_messages_action,
            "NotifyEVChargingNeeds": self._handle_notify_ev_charging_needs_action,
            "NotifyEVChargingSchedule": self._handle_notify_ev_charging_schedule_action,
            "NotifyEvent": self._handle_notify_event_action,
            "NotifyMonitoringReport": self._handle_notify_monitoring_report_action,
            "NotifyReport": self._handle_notify_report_action,
            "CostUpdated": self._handle_cost_updated_action,
            "PublishFirmwareStatusNotification": self._handle_publish_firmware_status_notification_action,
            "ReportChargingProfiles": self._handle_report_charging_profiles_action,
            "DiagnosticsStatusNotification": self._handle_diagnostics_status_notification_action,
            "LogStatusNotification": self._handle_log_status_notification_action,
            "StartTransaction": self._handle_start_transaction_action,
            "StopTransaction": self._handle_stop_transaction_action,
            "FirmwareStatusNotification": self._handle_firmware_status_notification_action,
            "ReservationStatusUpdate": self._handle_reservation_status_update_action,
            "Get15118EVCertificate": self._handle_get_15118_ev_certificate_action,
            "GetCertificateStatus": self._handle_get_certificate_status_action,
            "SignCertificate": self._handle_sign_certificate_action,
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
        await self._forward_charge_point_message(action, raw)

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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "BootNotification")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "BootNotification")
    async def _handle_boot_notification_action(self, payload, msg_id, raw, text_data):
        current_time = datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "currentTime": current_time,
            "interval": 300,
            "status": "Accepted",
        }

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "DataTransfer")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "DataTransfer")
    async def _handle_data_transfer_action(self, payload, msg_id, raw, text_data):
        return await self._handle_data_transfer(msg_id, payload)

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
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
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Authorize")
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    async def _handle_meter_values_action(self, payload, msg_id, raw, text_data):
        await self._store_meter_values(payload, text_data)
        self.charger.last_meter_values = payload
        await database_sync_to_async(
            Charger.objects.filter(pk=self.charger.pk).update
        )(last_meter_values=payload)
        return {}

    @protocol_call(
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "ClearedChargingLimit",
    )
    @protocol_call(
        "ocpp21",
        ProtocolCallModel.CP_TO_CSMS,
        "ClearedChargingLimit",
    )
    async def _handle_cleared_charging_limit_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        evse_id_value = payload_data.get("evseId")
        try:
            evse_id = int(evse_id_value) if evse_id_value is not None else None
        except (TypeError, ValueError):
            evse_id = None
        source_value = str(payload_data.get("chargingLimitSource") or "").strip()
        details: list[str] = []
        if source_value:
            details.append(f"source={source_value}")
        if evse_id is not None:
            details.append(f"evseId={evse_id}")
        message = "ClearedChargingLimit"
        if details:
            message += f": {', '.join(details)}"

        store.add_log(self.store_key, message, log_type="charger")

        def _persist_cleared_limit() -> None:
            target = getattr(self, "aggregate_charger", None) or getattr(
                self, "charger", None
            )
            connector_hint = getattr(self, "connector_value", None)
            if target is None and getattr(self, "charger_id", None):
                target = (
                    Charger.objects.filter(
                        charger_id=self.charger_id,
                        connector_id=connector_hint,
                    ).first()
                    or Charger.objects.filter(
                        charger_id=self.charger_id, connector_id__isnull=True
                    ).first()
                )
            if target is None and getattr(self, "charger_id", None):
                target, _created = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=connector_hint,
                )
            if target is None:
                return

            ClearedChargingLimitEvent.objects.create(
                charger=target,
                ocpp_message_id=msg_id or "",
                evse_id=evse_id,
                charging_limit_source=source_value,
                raw_payload=payload_data,
            )

        await database_sync_to_async(_persist_cleared_limit)()
        return {}

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
                component_data = entry.get("component") if isinstance(entry.get("component"), dict) else {}
                variable_data = entry.get("variable") if isinstance(entry.get("variable"), dict) else {}

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
            reservation_pk = int(reservation_value) if reservation_value is not None else None
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
                reservation_query = CPReservation.objects.select_related("connector").filter(
                    pk=reservation_pk
                )
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyChargingLimit")
    async def _handle_notify_charging_limit_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        charging_limit = payload_data.get("chargingLimit")
        if not isinstance(charging_limit, dict):
            charging_limit = {}
        source_value = str(charging_limit.get("chargingLimitSource") or "").strip()
        grid_critical_value = charging_limit.get("isGridCritical")
        grid_critical = None
        if grid_critical_value is not None:
            grid_critical = bool(grid_critical_value)
        schedules = payload_data.get("chargingSchedule")
        if not isinstance(schedules, list):
            schedules = []
        evse_id_value = payload_data.get("evseId")
        try:
            evse_id = int(evse_id_value) if evse_id_value is not None else None
        except (TypeError, ValueError):
            evse_id = None

        details: list[str] = []
        if source_value:
            details.append(f"source={source_value}")
        if grid_critical is not None:
            details.append(f"gridCritical={'yes' if grid_critical else 'no'}")
        if evse_id is not None:
            details.append(f"evseId={evse_id}")
        if schedules:
            details.append(f"schedules={len(schedules)}")
        message = "NotifyChargingLimit"
        if details:
            message += f": {', '.join(details)}"
        store.add_log(self.store_key, message, log_type="charger")

        normalized_payload: dict[str, object] = {
            "chargingLimit": charging_limit,
            "chargingSchedule": schedules,
        }
        if evse_id is not None:
            normalized_payload["evseId"] = evse_id

        received_at = timezone.now()

        def _persist_limit() -> None:
            target = getattr(self, "aggregate_charger", None) or getattr(
                self, "charger", None
            )
            connector_hint = getattr(self, "connector_value", None)
            if target is None and getattr(self, "charger_id", None):
                target = (
                    Charger.objects.filter(
                        charger_id=self.charger_id, connector_id=connector_hint
                    ).first()
                    or Charger.objects.filter(
                        charger_id=self.charger_id, connector_id__isnull=True
                    ).first()
                )
            if target is None and getattr(self, "charger_id", None):
                target, _created = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=connector_hint,
                )
            if target is None:
                return

            updates: dict[str, object] = {
                "last_charging_limit": normalized_payload,
                "last_charging_limit_source": source_value,
                "last_charging_limit_at": received_at,
            }
            if grid_critical is not None:
                updates["last_charging_limit_is_grid_critical"] = grid_critical

            Charger.objects.filter(pk=target.pk).update(**updates)
            for field, value in updates.items():
                setattr(target, field, value)

        await database_sync_to_async(_persist_limit)()
        return {}

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
        workflow_identifier = ":".join([bit for bit in identifier_bits if bit]) or "unknown"

        try:
            from apps.flows.models import Transition

            Transition.objects.create(
                workflow="customer-care.customer-information",
                identifier=workflow_identifier,
                from_state="pending",
                to_state="partial" if tbc else "acknowledged",
                step_name=data_text[:255] if data_text else "acknowledged",
                occurred_at=notified_at,
            )
        except Exception:  # pragma: no cover - defensive safeguard
            logger.exception("Unable to route customer-care acknowledgement")

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyDisplayMessages")
    async def _handle_notify_display_messages_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        request_id_value = payload_data.get("requestId")
        tbc_value = payload_data.get("tbc")
        try:
            request_id = int(request_id_value) if request_id_value is not None else None
        except (TypeError, ValueError):
            request_id = None
        tbc = bool(tbc_value) if tbc_value is not None else False
        received_at = timezone.now()
        message_info = payload_data.get("messageInfo")
        if not isinstance(message_info, (list, tuple)):
            message_info = []

        compliance_messages: list[dict[str, object]] = []

        def _persist_display_messages() -> None:
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

            notification = None
            if request_id is not None:
                notification = DisplayMessageNotification.objects.filter(
                    charger=charger,
                    request_id=request_id,
                    completed_at__isnull=True,
                ).order_by("-received_at").first()
            if notification is None:
                notification = DisplayMessageNotification.objects.create(
                    charger=charger,
                    ocpp_message_id=msg_id or "",
                    request_id=request_id,
                    tbc=tbc,
                    raw_payload=payload_data,
                )
            updates: dict[str, object] = {"tbc": tbc}
            if not tbc:
                updates["completed_at"] = received_at
            DisplayMessageNotification.objects.filter(pk=notification.pk).update(
                **updates
            )
            for field, value in updates.items():
                setattr(notification, field, value)

            for entry in message_info:
                if not isinstance(entry, dict):
                    continue
                message_id_value = entry.get("messageId")
                try:
                    message_id = (
                        int(message_id_value)
                        if message_id_value is not None
                        else None
                    )
                except (TypeError, ValueError):
                    message_id = None
                message_payload = entry.get("message") or {}
                if not isinstance(message_payload, dict):
                    message_payload = {}
                content_value = (
                    message_payload.get("content")
                    or message_payload.get("text")
                    or entry.get("content")
                    or ""
                )
                language_value = (
                    message_payload.get("language") or entry.get("language") or ""
                )
                component = entry.get("component") or {}
                variable = entry.get("variable") or {}
                if not isinstance(component, dict):
                    component = {}
                if not isinstance(variable, dict):
                    variable = {}
                compliance_messages.append(
                    {
                        "message_id": message_id,
                        "priority": str(entry.get("priority") or ""),
                        "state": str(entry.get("state") or ""),
                        "valid_from": _parse_ocpp_timestamp(entry.get("validFrom")),
                        "valid_to": _parse_ocpp_timestamp(entry.get("validTo")),
                        "language": str(language_value or ""),
                        "content": str(content_value or ""),
                    }
                )
                DisplayMessage.objects.create(
                    notification=notification,
                    charger=charger,
                    message_id=message_id,
                    priority=str(entry.get("priority") or ""),
                    state=str(entry.get("state") or ""),
                    valid_from=_parse_ocpp_timestamp(entry.get("validFrom")),
                    valid_to=_parse_ocpp_timestamp(entry.get("validTo")),
                    language=str(language_value or ""),
                    content=str(content_value or ""),
                    component_name=str(component.get("name") or ""),
                    component_instance=str(component.get("instance") or ""),
                    variable_name=str(variable.get("name") or ""),
                    variable_instance=str(variable.get("instance") or ""),
                    raw_payload=entry,
                )

        await database_sync_to_async(_persist_display_messages)()
        store.record_display_message_compliance(
            self.charger_id,
            request_id=request_id,
            tbc=tbc,
            messages=compliance_messages,
            received_at=received_at,
        )
        return {}

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
            requested_energy = _parse_energy(dc_params.get("maxEnergyAtChargingStation"))

        departure_time = _parse_ocpp_timestamp(charging_needs.get("departureTime"))
        received_at = timezone.now()

        log_parts = [f"evseId={evse_id}"]
        if requested_energy is not None:
            log_parts.append(f"energy={requested_energy}")
        if departure_time is not None:
            log_parts.append(f"departure={departure_time.isoformat()}")
        store.add_log(
            self.store_key,
            "NotifyEVChargingNeeds" + (": " + ", ".join(log_parts) if log_parts else ""),
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
        charging_rate_unit = str(charging_schedule.get("chargingRateUnit") or "").strip()
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent")
    async def _handle_notify_event_action(self, payload, msg_id, raw, text_data):
        payload_data = payload if isinstance(payload, dict) else {}
        event_entries = payload_data.get("eventData")

        generated_at = _parse_ocpp_timestamp(payload_data.get("generatedAt"))
        received_at = timezone.now()

        try:
            seq_no = int(payload_data.get("seqNo")) if "seqNo" in payload_data else None
        except (TypeError, ValueError):
            seq_no = None
        tbc = bool(payload_data.get("tbc")) if "tbc" in payload_data else False

        def _parse_int(value: object | None) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _clean_text(value: object | None) -> str | None:
            text = str(value or "").strip()
            return text or None

        if not isinstance(event_entries, (list, tuple)):
            store.add_log(self.store_key, "NotifyEvent: missing eventData", log_type="charger")
            return {}

        forwarded = 0
        for entry in event_entries:
            if not isinstance(entry, dict):
                continue

            component = entry.get("component") if isinstance(entry.get("component"), dict) else {}
            variable = entry.get("variable") if isinstance(entry.get("variable"), dict) else {}
            evse = component.get("evse") if isinstance(component.get("evse"), dict) else {}

            event_timestamp = _parse_ocpp_timestamp(entry.get("timestamp"))
            if event_timestamp is None:
                event_timestamp = generated_at or received_at

            connector_value = evse.get("connectorId") if isinstance(evse, dict) else None
            normalized_event = {
                "charger_id": getattr(self, "charger_id", None) or self.store_key,
                "connector_id": store.connector_slug(
                    connector_value if connector_value is not None else getattr(self, "connector_value", None)
                ),
                "evse_id": _parse_int(evse.get("id")),
                "event_id": _parse_int(entry.get("eventId")),
                "event_type": _clean_text(entry.get("eventType")),
                "trigger": _clean_text(entry.get("trigger")),
                "severity": _parse_int(entry.get("severity")),
                "actual_value": _clean_text(entry.get("actualValue")),
                "cause": _clean_text(entry.get("cause")),
                "tech_code": _clean_text(entry.get("techCode")),
                "tech_info": _clean_text(entry.get("techInfo")),
                "cleared": bool(entry.get("cleared")) if "cleared" in entry else False,
                "transaction_id": _clean_text(entry.get("transactionId")),
                "variable_monitoring_id": _parse_int(entry.get("variableMonitoringId")),
                "component_name": _clean_text(component.get("name") if component else None),
                "component_instance": _clean_text(component.get("instance") if component else None),
                "variable_name": _clean_text(variable.get("name") if variable else None),
                "variable_instance": _clean_text(variable.get("instance") if variable else None),
                "generated_at": generated_at or received_at,
                "event_timestamp": event_timestamp,
                "seq_no": seq_no,
                "tbc": tbc,
                "received_at": received_at,
            }

            store.forward_event_to_observability(normalized_event)
            forwarded += 1

        details: list[str] = []
        if seq_no is not None:
            details.append(f"seqNo={seq_no}")
        details.append(f"events={forwarded}")
        if generated_at is not None:
            details.append(f"generatedAt={generated_at.isoformat()}")

        store.add_log(
            self.store_key,
            "NotifyEvent" + (": " + ", ".join(details) if details else ""),
            log_type="charger",
        )
        return {}

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyMonitoringReport")
    async def _handle_notify_monitoring_report_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        request_id_value = payload_data.get("requestId")
        seq_no_value = payload_data.get("seqNo")
        generated_at = _parse_ocpp_timestamp(payload_data.get("generatedAt"))
        tbc_value = payload_data.get("tbc")
        received_at = timezone.now()
        try:
            request_id = int(request_id_value) if request_id_value is not None else None
        except (TypeError, ValueError):
            request_id = None
        try:
            seq_no = int(seq_no_value) if seq_no_value is not None else None
        except (TypeError, ValueError):
            seq_no = None
        tbc = bool(tbc_value) if tbc_value is not None else False
        monitoring_data = payload_data.get("monitoringData")
        if not isinstance(monitoring_data, (list, tuple)):
            monitoring_data = []

        normalized_records: list[dict[str, object]] = []

        def _persist_monitoring_report() -> None:
            charger = None
            if self.charger and getattr(self.charger, "pk", None):
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

            MonitoringReport.objects.create(
                charger=charger,
                request_id=request_id,
                seq_no=seq_no,
                generated_at=generated_at,
                tbc=tbc,
                raw_payload=payload_data,
            )

            for entry in monitoring_data:
                if not isinstance(entry, dict):
                    continue
                component_data = entry.get("component")
                variable_data = entry.get("variable")
                if not isinstance(component_data, dict) or not isinstance(variable_data, dict):
                    continue
                component_name = str(component_data.get("name") or "").strip()
                variable_name = str(variable_data.get("name") or "").strip()
                if not component_name or not variable_name:
                    continue
                component_instance = str(component_data.get("instance") or "").strip()
                variable_instance = str(variable_data.get("instance") or "").strip()
                component_evse = component_data.get("evse")
                evse_id = None
                connector_id = None
                if isinstance(component_evse, dict):
                    try:
                        evse_id = int(component_evse.get("id"))
                    except (TypeError, ValueError):
                        evse_id = None
                    connector_id = component_evse.get("connectorId")
                variable_obj, _created = Variable.objects.get_or_create(
                    charger=charger,
                    component_name=component_name,
                    component_instance=component_instance,
                    variable_name=variable_name,
                    variable_instance=variable_instance,
                    attribute_type="",
                )

                variable_monitoring = entry.get("variableMonitoring")
                if not isinstance(variable_monitoring, (list, tuple)):
                    continue
                for monitor in variable_monitoring:
                    if not isinstance(monitor, dict):
                        continue
                    monitoring_id_value = monitor.get("id") or monitor.get("monitoringId")
                    try:
                        monitoring_id = (
                            int(monitoring_id_value)
                            if monitoring_id_value is not None
                            else None
                        )
                    except (TypeError, ValueError):
                        monitoring_id = None
                    if monitoring_id is None:
                        continue
                    severity_value = monitor.get("severity")
                    try:
                        severity = (
                            int(severity_value) if severity_value is not None else None
                        )
                    except (TypeError, ValueError):
                        severity = None
                    threshold_value = monitor.get("value")
                    threshold_text = (
                        str(threshold_value) if threshold_value is not None else ""
                    )
                    monitor_type = str(monitor.get("type") or "").strip()
                    transaction_value = monitor.get("transaction")
                    is_transaction = bool(transaction_value) if transaction_value is not None else False
                    MonitoringRule.objects.update_or_create(
                        charger=charger,
                        monitoring_id=monitoring_id,
                        defaults={
                            "variable": variable_obj,
                            "severity": severity,
                            "monitor_type": monitor_type,
                            "threshold": threshold_text,
                            "is_transaction": is_transaction,
                            "is_active": True,
                            "raw_payload": monitor,
                        },
                    )

                    normalized_records.append(
                        {
                            "charger_id": charger.charger_id,
                            "request_id": request_id,
                            "seq_no": seq_no,
                            "generated_at": generated_at,
                            "tbc": tbc,
                            "component_name": component_name,
                            "component_instance": component_instance,
                            "variable_name": variable_name,
                            "variable_instance": variable_instance,
                            "monitoring_id": monitoring_id,
                            "severity": severity,
                            "monitor_type": monitor_type,
                            "threshold": threshold_text,
                            "is_transaction": is_transaction,
                            "evse_id": evse_id,
                            "connector_id": connector_id,
                        }
                    )

        await database_sync_to_async(_persist_monitoring_report)()
        for record in normalized_records:
            store.record_monitoring_report(
                record.get("charger_id"),
                request_id=record.get("request_id"),
                seq_no=record.get("seq_no"),
                generated_at=record.get("generated_at"),
                tbc=record.get("tbc", False),
                component_name=record.get("component_name", ""),
                component_instance=record.get("component_instance", ""),
                variable_name=record.get("variable_name", ""),
                variable_instance=record.get("variable_instance", ""),
                monitoring_id=record.get("monitoring_id"),
                severity=record.get("severity"),
                monitor_type=record.get("monitor_type", ""),
                threshold=record.get("threshold", ""),
                is_transaction=record.get("is_transaction", False),
                evse_id=record.get("evse_id"),
                connector_id=record.get("connector_id"),
                received_at=received_at,
            )
        if request_id is not None and not tbc:
            store.pop_monitoring_report_request(request_id)
        self._log_ocpp201_notification("NotifyMonitoringReport", payload)
        return {}

    @protocol_call(
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "PublishFirmwareStatusNotification",
    )
    async def _handle_publish_firmware_status_notification_action(
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
                deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "ReportChargingProfiles")
    async def _handle_report_charging_profiles_action(
        self, payload, msg_id, raw, text_data
    ):
        payload_data = payload if isinstance(payload, dict) else {}
        request_id_value = payload_data.get("requestId")
        evse_value = payload_data.get("evseId")
        charging_profile = payload_data.get("chargingProfile")
        tbc = bool(payload_data.get("tbc")) if payload_data.get("tbc") is not None else False

        def _parse_int(value: object | None) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        try:
            request_id = _parse_int(request_id_value)
        except Exception:  # pragma: no cover - defensive
            request_id = None

        evse_id = _parse_int(evse_value)

        if not isinstance(charging_profile, dict):
            store.add_log(
                self.store_key,
                "ReportChargingProfiles: missing chargingProfile payload",
                log_type="charger",
            )
            return {}

        def _normalize_schedule(data: dict | None) -> dict[str, object]:
            if not isinstance(data, dict):
                return {}

            normalized: dict[str, object] = {}

            rate_unit = str(data.get("chargingRateUnit") or "").strip()
            if rate_unit:
                normalized["chargingRateUnit"] = rate_unit

            duration = _parse_int(data.get("duration"))
            if duration is not None:
                normalized["duration"] = duration

            start_schedule = _parse_ocpp_timestamp(data.get("startSchedule"))
            if start_schedule is not None:
                normalized["startSchedule"] = start_schedule.isoformat()

            try:
                min_charging_rate = (
                    float(data.get("minChargingRate"))
                    if data.get("minChargingRate") is not None
                    else None
                )
            except (TypeError, ValueError):
                min_charging_rate = None
            if min_charging_rate is not None:
                normalized["minChargingRate"] = min_charging_rate

            periods: list[dict[str, object]] = []
            entries = data.get("chargingSchedulePeriod")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    start_period = _parse_int(entry.get("startPeriod"))
                    try:
                        limit = float(entry.get("limit"))
                    except (TypeError, ValueError):
                        continue
                    period: dict[str, object] = {
                        "startPeriod": start_period,
                        "limit": limit,
                    }
                    number_phases = _parse_int(entry.get("numberPhases"))
                    if number_phases is not None:
                        period["numberPhases"] = number_phases
                    phase_to_use = _parse_int(entry.get("phaseToUse"))
                    if phase_to_use is not None:
                        period["phaseToUse"] = phase_to_use
                    periods.append(period)

            periods.sort(key=lambda entry: entry.get("startPeriod") or 0)
            if periods:
                normalized["periods"] = periods

            return normalized

        def _normalize_profile(data: dict[str, object]) -> dict[str, object]:
            normalized: dict[str, object] = {}

            profile_id = _parse_int(
                data.get("chargingProfileId") or data.get("id")
            )
            if profile_id is not None:
                normalized["id"] = profile_id

            stack_level = _parse_int(data.get("stackLevel"))
            if stack_level is not None:
                normalized["stackLevel"] = stack_level

            purpose_value = str(data.get("chargingProfilePurpose") or "").strip()
            if purpose_value:
                normalized["purpose"] = purpose_value

            kind_value = str(data.get("chargingProfileKind") or "").strip()
            if kind_value:
                normalized["kind"] = kind_value

            recurrency = str(data.get("recurrencyKind") or "").strip()
            if recurrency:
                normalized["recurrencyKind"] = recurrency

            transaction_id = _parse_int(data.get("transactionId"))
            if transaction_id is not None:
                normalized["transactionId"] = transaction_id

            valid_from = _parse_ocpp_timestamp(data.get("validFrom"))
            if valid_from is not None:
                normalized["validFrom"] = valid_from.isoformat()

            valid_to = _parse_ocpp_timestamp(data.get("validTo"))
            if valid_to is not None:
                normalized["validTo"] = valid_to.isoformat()

            normalized["schedule"] = _normalize_schedule(
                data.get("chargingSchedule")
            )

            return normalized

        def _diff_profiles(expected_profile, reported_profile: dict[str, object]):
            expected_payload = {
                "chargingProfileId": expected_profile.charging_profile_id,
                "stackLevel": expected_profile.stack_level,
                "chargingProfilePurpose": expected_profile.purpose,
                "chargingProfileKind": expected_profile.kind,
                "recurrencyKind": expected_profile.recurrency_kind,
                "transactionId": expected_profile.transaction_id,
                "validFrom": expected_profile.valid_from,
                "validTo": expected_profile.valid_to,
                "chargingSchedule": expected_profile._schedule_payload(),
            }

            expected_normalized = _normalize_profile(expected_payload)
            reported_normalized = _normalize_profile(reported_profile)

            mismatches: list[str] = []

            def _compare_field(key: str, label: str) -> None:
                expected_value = expected_normalized.get(key)
                reported_value = reported_normalized.get(key)
                if expected_value != reported_value:
                    mismatches.append(
                        f"{label} expected {expected_value} got {reported_value}"
                    )

            _compare_field("stackLevel", "stack level")
            _compare_field("purpose", "purpose")
            _compare_field("kind", "kind")
            _compare_field("recurrencyKind", "recurrency kind")
            _compare_field("transactionId", "transaction id")
            _compare_field("validFrom", "valid from")
            _compare_field("validTo", "valid to")

            expected_schedule = expected_normalized.get("schedule", {})
            reported_schedule = reported_normalized.get("schedule", {})

            if expected_schedule.get("chargingRateUnit") != reported_schedule.get(
                "chargingRateUnit"
            ):
                mismatches.append(
                    "charging rate unit expected "
                    f"{expected_schedule.get('chargingRateUnit')} got "
                    f"{reported_schedule.get('chargingRateUnit')}"
                )

            if expected_schedule.get("duration") != reported_schedule.get("duration"):
                mismatches.append(
                    f"duration expected {expected_schedule.get('duration')} got "
                    f"{reported_schedule.get('duration')}"
                )

            if expected_schedule.get("startSchedule") != reported_schedule.get(
                "startSchedule"
            ):
                mismatches.append(
                    "start schedule expected "
                    f"{expected_schedule.get('startSchedule')} got "
                    f"{reported_schedule.get('startSchedule')}"
                )

            if expected_schedule.get("minChargingRate") != reported_schedule.get(
                "minChargingRate"
            ):
                mismatches.append(
                    "min charging rate expected "
                    f"{expected_schedule.get('minChargingRate')} got "
                    f"{reported_schedule.get('minChargingRate')}"
                )

            expected_periods = expected_schedule.get("periods", [])
            reported_periods = reported_schedule.get("periods", [])

            if len(expected_periods) != len(reported_periods):
                mismatches.append(
                    f"period count expected {len(expected_periods)} got {len(reported_periods)}"
                )
            else:
                for index, (expected_period, reported_period) in enumerate(
                    zip(expected_periods, reported_periods), start=1
                ):
                    if expected_period != reported_period:
                        mismatches.append(
                            "period %s expected %s got %s"
                            % (index, expected_period, reported_period)
                        )

            return mismatches

        def _reconcile_profiles() -> None:
            charger = self.charger
            if charger is None and self.charger_id:
                charger = Charger.objects.filter(charger_id=self.charger_id).first()
            if charger is None:
                return

            profile_id = _parse_int(
                charging_profile.get("chargingProfileId")
                or charging_profile.get("id")
            )

            evse_label = store.connector_slug(evse_id)

            if profile_id is not None:
                store.record_reported_charging_profile(
                    charger.charger_id,
                    request_id=request_id,
                    evse_id=evse_id,
                    profile_id=profile_id,
                )

            expected_profiles = ChargingProfile.objects.filter(
                charger__charger_id=charger.charger_id
            )
            if evse_id is not None:
                expected_profiles = expected_profiles.filter(
                    charger__connector_id=evse_id
                )
            expected_by_id = {
                entry.charging_profile_id: entry for entry in expected_profiles
            }

            mismatches: list[str] = []

            if profile_id is None:
                mismatches.append("missing chargingProfileId")
            else:
                expected_profile = expected_by_id.get(profile_id)
                if expected_profile is None:
                    mismatches.append(
                        f"unexpected profile {profile_id} reported for evse {evse_label}"
                    )
                else:
                    mismatches.extend(_diff_profiles(expected_profile, charging_profile))

            if mismatches:
                prefix = "ReportChargingProfiles mismatch"
                details = ", ".join(mismatches)
                request_label = (
                    f"request {request_id}" if request_id is not None else "request ?"
                )
                store.add_log(
                    self.store_key,
                    f"{prefix} ({request_label}, evse {evse_label}): {details}",
                    log_type="charger",
                )

            if tbc:
                return

            recorded = store.consume_reported_charging_profiles(
                charger.charger_id, request_id=request_id
            )
            reported_by_evse = recorded.get("reported") if recorded else {}

            expected_all = ChargingProfile.objects.filter(
                charger__charger_id=charger.charger_id
            )
            expected_by_evse: dict[str, set[int]] = {}
            for entry in expected_all:
                key = store.connector_slug(entry.connector_id)
                expected_by_evse.setdefault(key, set()).add(entry.charging_profile_id)

            for evse_key, expected_ids in expected_by_evse.items():
                reported_ids = reported_by_evse.get(evse_key, set())
                missing = sorted(expected_ids - set(reported_ids))
                if not missing:
                    continue
                request_label = (
                    f"request {request_id}" if request_id is not None else "request ?"
                )
                store.add_log(
                    self.store_key,
                    f"ReportChargingProfiles missing ({request_label}, evse {evse_key}): "
                    + ", ".join(str(value) for value in missing),
                    log_type="charger",
                )

        await database_sync_to_async(_reconcile_profiles)()
        self._log_ocpp201_notification("ReportChargingProfiles", payload)
        return {}

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "SecurityEventNotification")
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Get15118EVCertificate")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Get15118EVCertificate")
    async def _handle_get_15118_ev_certificate_action(
        self, payload, msg_id, raw, text_data
    ):
        certificate_type = str(payload.get("certificateType") or "").strip()
        csr_value = payload.get("exiRequest") or payload.get("csr")
        if csr_value is None:
            csr_value = ""
        if not isinstance(csr_value, str):
            csr_value = str(csr_value)
        csr_value = csr_value.strip()

        def _csr_is_valid(value: str) -> bool:
            return bool(value)

        responded_at = timezone.now()

        def _handle_request():
            target = self._resolve_certificate_target()
            response_payload: dict[str, object]
            exi_response = ""
            request_status = CertificateRequest.STATUS_REJECTED
            status_info = ""

            if target is None:
                status_info = "Unknown charge point."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "Failed",
                        "additionalInfo": status_info,
                    },
                }
            elif not _csr_is_valid(csr_value):
                status_info = "EXI request payload is missing or invalid."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "FormatViolation",
                        "additionalInfo": status_info,
                    },
                }
            else:
                try:
                    exi_response = certificate_signing.sign_certificate_request(
                        csr=csr_value,
                        certificate_type=certificate_type,
                        charger_id=target.charger_id,
                    )
                    response_payload = {
                        "status": "Accepted",
                        "exiResponse": exi_response,
                    }
                    request_status = CertificateRequest.STATUS_ACCEPTED
                    status_info = ""
                except certificate_signing.CertificateSigningError as exc:
                    status_info = str(exc) or "Certificate request failed."
                    response_payload = {
                        "status": "Rejected",
                        "statusInfo": {
                            "reasonCode": "Failed",
                            "additionalInfo": status_info,
                        },
                    }
                    request_status = CertificateRequest.STATUS_ERROR

            request_pk: int | None = None
            if target is not None:
                request = CertificateRequest.objects.create(
                    charger=target,
                    action=CertificateRequest.ACTION_15118,
                    certificate_type=certificate_type,
                    csr=csr_value,
                    signed_certificate=exi_response,
                    status=request_status,
                    status_info=status_info,
                    request_payload=payload,
                    response_payload=response_payload,
                    responded_at=responded_at,
                )
                request_pk = request.pk

            return {
                "response": response_payload,
                "status": request_status,
                "request_pk": request_pk,
            }

        result = await database_sync_to_async(_handle_request)()
        response_payload = result.get("response", {})
        status_value = response_payload.get("status") or "Unknown"
        store.add_log(
            self.store_key,
            f"Get15118EVCertificate request processed (status={status_value}).",
            log_type="charger",
        )
        return response_payload

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "GetCertificateStatus")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "GetCertificateStatus")
    async def _handle_get_certificate_status_action(
        self, payload, msg_id, raw, text_data
    ):
        hash_data = payload.get("certificateHashData") or {}
        if not isinstance(hash_data, dict):
            hash_data = {}

        def _persist_status() -> dict:
            target = self._resolve_certificate_target()
            status_value = "Failed"
            status_info = "Unknown charge point."
            response_payload: dict[str, object] = {"status": status_value}

            if target is not None:
                status_info = "Certificate not found."
                installed = InstalledCertificate.objects.filter(
                    charger=target, certificate_hash_data=hash_data
                ).first()
                if installed and installed.status == InstalledCertificate.STATUS_INSTALLED:
                    status_value = "Accepted"
                    status_info = ""
                    response_payload = {"status": status_value}
                else:
                    response_payload = {
                        "status": status_value,
                        "statusInfo": {
                            "reasonCode": "NotFound",
                            "additionalInfo": status_info,
                        },
                    }

                CertificateStatusCheck.objects.create(
                    charger=target,
                    certificate_hash_data=hash_data,
                    ocsp_result={},
                    status=(
                        CertificateStatusCheck.STATUS_ACCEPTED
                        if status_value == "Accepted"
                        else CertificateStatusCheck.STATUS_REJECTED
                    ),
                    status_info=status_info,
                    request_payload=payload,
                    response_payload=response_payload,
                    responded_at=timezone.now(),
                )

            return {
                "response": response_payload,
                "status": status_value,
                "status_info": status_info,
            }

        result = await database_sync_to_async(_persist_status)()
        status_value = result.get("status")
        store.add_log(
            self.store_key,
            f"GetCertificateStatus request received (status={status_value}).",
            log_type="charger",
        )
        return result.get("response")

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "SignCertificate")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "SignCertificate")
    async def _handle_sign_certificate_action(
        self, payload, msg_id, raw, text_data
    ):
        csr_value = payload.get("csr")
        if csr_value is None:
            csr_value = ""
        if not isinstance(csr_value, str):
            csr_value = str(csr_value)
        csr_value = csr_value.strip()
        certificate_type = str(payload.get("certificateType") or "").strip()

        def _csr_is_valid(value: str) -> bool:
            return bool(value)

        responded_at = timezone.now()

        def _handle_signing():
            target = self._resolve_certificate_target()
            response_payload: dict[str, object]
            signed_certificate = ""
            request_status = CertificateRequest.STATUS_REJECTED
            status_info = ""
            request_pk: int | None = None

            if target is None:
                status_info = "Unknown charge point."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "Failed",
                        "additionalInfo": status_info,
                    },
                }
            elif not _csr_is_valid(csr_value):
                status_info = "CSR payload is missing or invalid."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "FormatViolation",
                        "additionalInfo": status_info,
                    },
                }
            else:
                try:
                    signed_certificate = certificate_signing.sign_certificate_request(
                        csr=csr_value,
                        certificate_type=certificate_type,
                        charger_id=target.charger_id,
                    )
                    response_payload = {"status": "Accepted"}
                    request_status = CertificateRequest.STATUS_ACCEPTED
                    status_info = ""
                except certificate_signing.CertificateSigningError as exc:
                    status_info = str(exc) or "Certificate signing failed."
                    response_payload = {
                        "status": "Rejected",
                        "statusInfo": {
                            "reasonCode": "Failed",
                            "additionalInfo": status_info,
                        },
                    }
                    request_status = CertificateRequest.STATUS_ERROR

            if target is not None:
                request = CertificateRequest.objects.create(
                    charger=target,
                    action=CertificateRequest.ACTION_SIGN,
                    certificate_type=certificate_type,
                    csr=csr_value,
                    signed_certificate=signed_certificate,
                    status=request_status,
                    status_info=status_info,
                    request_payload=payload,
                    response_payload=response_payload,
                    responded_at=responded_at,
                )
                request_pk = request.pk

            return {
                "response": response_payload,
                "target": target,
                "request_pk": request_pk,
                "signed_certificate": signed_certificate,
            }

        result = await database_sync_to_async(_handle_signing)()
        response_payload: dict[str, object] = result.get("response", {})
        status_value = str(response_payload.get("status") or "Unknown")
        target: Charger | None = result.get("target")
        signed_certificate = result.get("signed_certificate") or ""
        request_pk = result.get("request_pk")

        if (
            status_value.lower() == "accepted"
            and target is not None
            and signed_certificate
        ):
            await self._dispatch_certificate_signed(
                target,
                certificate_chain=signed_certificate,
                certificate_type=certificate_type,
                request_pk=request_pk,
            )

        store.add_log(
            self.store_key,
            f"SignCertificate request processed (status={status_value}).",
            log_type="charger",
        )
        return response_payload

    async def _dispatch_certificate_signed(
        self,
        charger: Charger,
        *,
        certificate_chain: str,
        certificate_type: str = "",
        request_pk: int | None = None,
    ) -> None:
        payload = {"certificateChain": certificate_chain}
        if certificate_type:
            payload["certificateType"] = certificate_type
        message_id = uuid.uuid4().hex
        msg = json.dumps([2, message_id, "CertificateSigned", payload])
        await self.send(msg)

        log_key = self.store_key or store.identity_key(
            charger.charger_id, getattr(charger, "connector_id", None)
        )
        requested_at = timezone.now()
        operation = await database_sync_to_async(CertificateOperation.objects.create)(
            charger=charger,
            action=CertificateOperation.ACTION_SIGNED,
            certificate_type=certificate_type,
            request_payload=payload,
            status=CertificateOperation.STATUS_PENDING,
        )
        if request_pk:
            await database_sync_to_async(CertificateRequest.objects.filter(pk=request_pk).update)(
                signed_certificate=certificate_chain,
                status=CertificateRequest.STATUS_PENDING,
                status_info="Certificate sent to charge point.",
            )
        store.register_pending_call(
            message_id,
            {
                "action": "CertificateSigned",
                "charger_id": charger.charger_id,
                "connector_id": getattr(charger, "connector_id", None),
                "log_key": log_key,
                "requested_at": requested_at,
                "operation_pk": operation.pk,
            },
        )
        store.schedule_call_timeout(
            message_id,
            action="CertificateSigned",
            log_key=log_key,
            message="CertificateSigned request timed out",
        )

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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "LogStatusNotification")
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

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "TransactionEvent")
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

        def _record_transaction_event(
            tx_obj: Transaction | None, extra: dict[str, object] | None = None
        ) -> None:
            notification: dict[str, object] = {
                "charger_id": getattr(self, "charger_id", None) or self.store_key,
                "connector_id": store.connector_slug(connector_value),
                "event_type": event_type,
                "timestamp": timestamp_value,
                "transaction_pk": getattr(tx_obj, "pk", None),
                "ocpp_transaction_id": ocpp_tx_id
                or getattr(tx_obj, "ocpp_transaction_id", None),
            }
            if transaction_info:
                if "meterStart" in transaction_info:
                    notification["meter_start"] = transaction_info.get("meterStart")
                if "meterStop" in transaction_info:
                    notification["meter_stop"] = transaction_info.get("meterStop")
            if extra:
                notification.update(extra)
            store.record_transaction_event(notification)

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
                _record_transaction_event(tx_obj)
                transaction_reference = ocpp_tx_id or tx_obj.ocpp_transaction_id or str(tx_obj.pk)
                store.mark_transaction_requests(
                    charger_id=self.charger_id,
                    connector_id=connector_value,
                    transaction_id=transaction_reference,
                    actions={"RequestStartTransaction"},
                    statuses={"accepted", "requested"},
                    status="started",
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
            _record_transaction_event(tx_obj)
            await self._update_consumption_message(tx_obj.pk)
            await self._cancel_consumption_message()
            transaction_reference = ocpp_tx_id or tx_obj.ocpp_transaction_id or str(tx_obj.pk)
            store.mark_transaction_requests(
                charger_id=self.charger_id,
                connector_id=connector_value,
                transaction_id=transaction_reference,
                actions={"RequestStartTransaction"},
                statuses={"started", "accepted", "requested"},
                status="completed",
            )
            store.mark_transaction_requests(
                charger_id=self.charger_id,
                connector_id=connector_value,
                transaction_id=transaction_reference,
                actions={"RequestStopTransaction"},
                statuses={"accepted", "requested"},
                status="completed",
            )
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
            _record_transaction_event(tx_obj)
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
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "FirmwareStatusNotification",
    )
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
