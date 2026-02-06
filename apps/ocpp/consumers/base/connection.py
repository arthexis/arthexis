import asyncio
import logging
from collections import deque
from datetime import datetime
import ipaddress

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.links.models import Reference
from apps.links.reference_utils import host_is_local_loopback
from config.offline import requires_network

from ... import store
from ...evcs_discovery import (
    DEFAULT_CONSOLE_PORT,
    HTTPS_PORTS,
    build_console_url,
    prioritise_ports,
    scan_open_ports,
)
from ...forwarder import forwarder
from ...models import Charger
from ...status_resets import STATUS_RESET_UPDATES, clear_cached_statuses
from .identity import _register_log_names_for_identity, _resolve_client_ip

logger = logging.getLogger(__name__)


class ConnectionMixin:
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
        self._charger_record_created = False
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
        self._charger_record_created = created
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

    async def _assign_connector(self, connector: int | str | None) -> None:
        """Ensure ``self.charger`` matches the provided connector id."""
        connector_value, is_valid = self._normalize_connector_value(connector)
        if not is_valid:
            return
        if connector_value is None:
            await self._assign_aggregate_connector()
            return
        await self._assign_specific_connector(connector_value)

    def _normalize_connector_value(
        self, connector: int | str | None
    ) -> tuple[int | None, bool]:
        if connector in (None, "", "-"):
            return None, True
        try:
            connector_value = int(connector)
        except (TypeError, ValueError):
            return None, False
        if connector_value == 0:
            return None, True
        return connector_value, True

    async def _assign_aggregate_connector(self) -> None:
        aggregate = await self._ensure_aggregate_charger()
        self.charger = aggregate
        new_key = store.identity_key(self.charger_id, None)
        await self._reassign_store_identity(new_key)
        aggregate_name = await sync_to_async(
            lambda: self.charger.name or self.charger.charger_id
        )()
        friendly_name = aggregate_name or self.charger_id
        _register_log_names_for_identity(self.charger_id, None, friendly_name)
        self.store_key = new_key
        self.connector_value = None
        await self._maybe_create_console_reference()

    async def _assign_specific_connector(self, connector_value: int) -> None:
        if (
            self.charger
            and self.connector_value == connector_value
            and self.charger.connector_id == connector_value
        ):
            return
        await self._ensure_aggregate_charger()
        self.charger = await self._get_or_create_connector_charger(
            connector_value,
            update_last_path=True,
        )
        new_key = store.identity_key(self.charger_id, connector_value)
        await self._reassign_store_identity(new_key)
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
    async def _ensure_aggregate_charger(self) -> Charger:
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
        return self.aggregate_charger

    async def _get_or_create_connector_charger(
        self, connector_value: int, *, update_last_path: bool
    ) -> Charger:
        def _get_or_create():
            charger, _ = Charger.objects.get_or_create(
                charger_id=self.charger_id,
                connector_id=connector_value,
                defaults={"last_path": self.scope.get("path", "")},
            )
            if update_last_path and self.scope.get("path"):
                path = self.scope.get("path")
                if charger.last_path != path:
                    charger.last_path = path
                    charger.save(update_fields=["last_path"])
            charger.refresh_manager_node()
            return charger

        return await database_sync_to_async(_get_or_create)()

    async def _reassign_store_identity(self, new_key: str) -> None:
        previous_key = self.store_key
        if previous_key == new_key:
            return
        existing_consumer = store.connections.get(new_key)
        if existing_consumer is not None and existing_consumer is not self:
            await existing_consumer.close()
        store.reassign_identity(previous_key, new_key)
        store.connections[new_key] = self
        store.logs["charger"].setdefault(
            new_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
        )

    async def _maybe_create_console_reference(self) -> None:
        if self._header_reference_created or not self.client_ip:
            return
        await database_sync_to_async(self._ensure_console_reference)()
        self._header_reference_created = True

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

    async def _reconnect_forwarding_session(
        self,
        charger,
        *,
        allowed_messages: tuple[str, ...] | None,
        forwarder_pk: int | None,
    ):
        """Attempt to re-establish a forwarding session for ``charger``."""

        if charger is None or not getattr(charger, "pk", None):
            return None, None

        def _refresh():
            return (
                Charger.objects.select_related("forwarded_to")
                .filter(pk=charger.pk)
                .first()
            )

        refreshed = await database_sync_to_async(_refresh)()
        if refreshed is None:
            return None, None

        target = getattr(refreshed, "forwarded_to", None)
        if target is None:
            return None, refreshed

        session = await sync_to_async(forwarder.connect_forwarding_session)(
            refreshed,
            target,
        )
        if session is None:
            return None, refreshed
        session.forwarded_messages = allowed_messages
        session.forwarder_id = forwarder_pk
        return session, refreshed

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
                "Failed to forward %s from charger %s via %s: %s",
                action,
                getattr(charger, "charger_id", charger.pk),
                getattr(session, "url", "unknown"),
                exc,
            )
            forwarder.remove_session(charger.pk)
            session, refreshed = await self._reconnect_forwarding_session(
                charger,
                allowed_messages=allowed,
                forwarder_pk=forwarder_pk,
            )
            if session is None:
                return
            context = await self._ensure_forwarding_context(refreshed)
            if context is None:
                forwarder.remove_session(charger.pk)
                return
            allowed, forwarder_pk = context
            session.forwarded_messages = allowed
            session.forwarder_id = forwarder_pk
            if allowed is not None and action not in allowed:
                return
            try:
                await sync_to_async(session.connection.send)(raw)
            except Exception as retry_exc:
                logger.warning(
                    "Failed to forward %s from charger %s after reconnect: %s",
                    action,
                    getattr(charger, "charger_id", charger.pk),
                    retry_exc,
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
        try:
            parsed_ip = ipaddress.ip_address(ip)
        except ValueError:
            return
        if not parsed_ip.is_global:
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
            if self._charger_record_created:
                return
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
