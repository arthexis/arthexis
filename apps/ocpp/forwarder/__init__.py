"""Class-based forwarding service aligned with the websocket consumer."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator, MutableMapping

from django.db.models import Q
from django.utils import timezone
from websocket import WebSocketException, create_connection

logger = logging.getLogger(__name__)


@dataclass
class ForwardingSession:
    """Active websocket forwarding session for a charge point."""

    charger_pk: int
    node_id: int | None
    url: str
    connection: object
    connected_at: datetime
    forwarder_id: int | None = None
    forwarded_messages: tuple[str, ...] | None = None
    forwarded_calls: tuple[str, ...] | None = None
    pending_call_ids: set[str] = field(default_factory=set)
    last_activity: datetime | None = None
    listener: threading.Thread | None = None

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.connection, "connected", False))


class Forwarder:
    """Stateful forwarding coordinator mirroring the websocket consumer."""

    def __init__(self) -> None:
        self._sessions: MutableMapping[int, ForwardingSession] = {}
        self._keepalive_task: asyncio.Task[None] | None = None
        self._keepalive_interval: int | None = None
        self._sync_lock = threading.Lock()

    @staticmethod
    def _candidate_forwarding_urls(node, charger) -> Iterator[str]:
        """Yield websocket URLs suitable for forwarding ``charger`` via ``node``."""

        if node is None or charger is None:
            return iter(())

        charger_id = (getattr(charger, "charger_id", "") or "").strip()
        if not charger_id:
            return iter(())

        from urllib.parse import quote, urlsplit, urlunsplit

        encoded_id = quote(charger_id, safe="")
        urls: list[str] = []
        for base in getattr(node, "iter_remote_urls", lambda _path: [])("/"):
            if not base:
                continue
            parsed = urlsplit(base)
            if parsed.scheme not in {"http", "https"}:
                continue
            hostname = parsed.hostname or ""
            if parsed.scheme == "https" and hostname:
                try:
                    ipaddress.ip_address(hostname)
                except ValueError:
                    pass
                else:
                    continue
            scheme = "wss" if parsed.scheme == "https" else "ws"
            base_path = parsed.path.rstrip("/")
            for prefix in ("", "/ws"):
                path = f"{base_path}{prefix}/{encoded_id}".replace("//", "/")
                if not path.startswith("/"):
                    path = f"/{path}"
                urls.append(urlunsplit((scheme, parsed.netloc, path, "", "")))
        return iter(urls)

    @staticmethod
    def _close_forwarding_session(session: ForwardingSession) -> None:
        """Close the websocket connection associated with ``session`` if open."""

        connection = session.connection
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # pragma: no cover - best effort close
            pass

    def get_session(self, charger_pk: int) -> ForwardingSession | None:
        """Return the forwarding session for ``charger_pk`` when present."""

        with self._sync_lock:
            return self._sessions.get(charger_pk)

    def iter_sessions(self) -> Iterator[ForwardingSession]:
        """Yield active forwarding sessions."""

        with self._sync_lock:
            return iter(list(self._sessions.values()))

    def clear_sessions(self) -> None:
        """Close and drop all active forwarding sessions."""

        with self._sync_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._close_forwarding_session(session)

    def remove_session(self, charger_pk: int) -> None:
        """Close and remove the session for ``charger_pk`` when it exists."""

        with self._sync_lock:
            session = self._sessions.pop(charger_pk, None)
        if session is not None:
            self._close_forwarding_session(session)

    def prune_inactive_sessions(self, active_ids: Iterable[int]) -> None:
        """Close sessions that no longer map to a charger in ``active_ids``."""

        valid = set(active_ids)
        with self._sync_lock:
            session_ids = list(self._sessions.keys())
        for pk in session_ids:
            if pk not in valid:
                self.remove_session(pk)

    def connect_forwarding_session(
        self, charger, target_node, *, timeout: float = 5.0
    ) -> ForwardingSession | None:
        """Establish a websocket forwarding session for ``charger``.

        Returns the created session or ``None`` when all connection attempts fail.
        """

        if getattr(charger, "pk", None) is None:
            return None

        for url in self._candidate_forwarding_urls(target_node, charger):
            try:
                connection = create_connection(
                    url,
                    timeout=timeout,
                    subprotocols=["ocpp1.6"],
                )
            except (WebSocketException, OSError, ValueError) as exc:
                logger.warning(
                    "Websocket forwarding connection to %s via %s failed: %s",
                    target_node,
                    url,
                    exc,
                )
                continue

            session = ForwardingSession(
                charger_pk=charger.pk,
                node_id=getattr(target_node, "pk", None),
                url=url,
                connection=connection,
                connected_at=timezone.now(),
                last_activity=timezone.now(),
            )
            with self._sync_lock:
                self._sessions[charger.pk] = session
            logger.info(
                "Established forwarding websocket for charger %s to %s via %s",
                getattr(charger, "charger_id", charger.pk),
                target_node,
                url,
            )
            self._start_listener(session)
            return session

        return None

    def keepalive_sessions(self, *, idle_seconds: int = 60) -> int:
        """Send ping frames on idle sessions to keep forwarding sockets open."""

        if idle_seconds <= 0:
            return 0

        now = timezone.now()
        pinged = 0
        with self._sync_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if not session.is_connected:
                self.remove_session(session.charger_pk)
                continue
            last_activity = session.last_activity or session.connected_at
            if (now - last_activity).total_seconds() < idle_seconds:
                continue
            ping = getattr(session.connection, "ping", None)
            if ping is None:
                continue
            try:
                ping()
            except (WebSocketException, OSError) as exc:  # pragma: no cover - network errors
                logger.warning(
                    "Forwarding websocket ping failed for charger %s via %s: %s",
                    session.charger_pk,
                    session.url,
                    exc,
                )
                self.remove_session(session.charger_pk)
                continue
            with self._sync_lock:
                current = self._sessions.get(session.charger_pk)
                if current is session:
                    session.last_activity = now
            pinged += 1
        return pinged

    def _start_listener(self, session: ForwardingSession) -> None:
        if not hasattr(session.connection, "recv"):
            return
        if session.listener and session.listener.is_alive():
            return
        listener = threading.Thread(
            target=self._listen_forwarding_session,
            args=(session.charger_pk,),
            daemon=True,
        )
        session.listener = listener
        listener.start()

    def _listen_forwarding_session(self, charger_pk: int) -> None:
        """Listen for incoming commands from the remote node."""

        from asgiref.sync import async_to_sync
        import json

        from apps.ocpp import store
        from apps.ocpp.models import Charger

        while True:
            session = self.get_session(charger_pk)
            if session is None or not session.is_connected:
                return
            try:
                raw = session.connection.recv()
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning(
                    "Forwarding websocket recv failed for charger %s via %s: %s",
                    charger_pk,
                    getattr(session, "url", "unknown"),
                    exc,
                )
                self.remove_session(charger_pk)
                return
            if not raw:
                continue
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("ocpp"), list):
                message = parsed.get("ocpp")
            else:
                message = parsed
            if not isinstance(message, list) or not message:
                continue
            message_type = message[0]
            if message_type != 2:
                continue
            if len(message) < 3:
                continue
            message_id = message[1]
            action = message[2]
            if not isinstance(message_id, str):
                message_id = str(message_id)
            if not isinstance(action, str):
                action = str(action)

            if session.forwarded_calls is not None and action not in session.forwarded_calls:
                error = json.dumps(
                    [
                        4,
                        message_id,
                        "SecurityError",
                        "Action not permitted by forwarding policy.",
                        {},
                    ]
                )
                try:
                    session.connection.send(error)
                except Exception as exc:  # pragma: no cover - network errors
                    logger.warning("Failed to send error to forwarding peer for charger %s: %s", charger_pk, exc)
                continue

            charger = Charger.objects.filter(pk=charger_pk).first()
            if charger is None:
                continue
            if not charger.allow_remote:
                error = json.dumps(
                    [
                        4,
                        message_id,
                        "SecurityError",
                        "Remote actions are disabled for this charge point.",
                        {},
                    ]
                )
                try:
                    session.connection.send(error)
                except Exception:  # pragma: no cover - network errors
                    pass
                continue
            ws = store.get_connection(charger.charger_id, charger.connector_id)
            if ws is None:
                error = json.dumps(
                    [
                        4,
                        message_id,
                        "InternalError",
                        "Charge point not connected.",
                        {},
                    ]
                )
                try:
                    session.connection.send(error)
                except Exception:  # pragma: no cover - network errors
                    pass
                continue

            payload = message[3] if len(message) > 3 else {}
            log_key = store.identity_key(charger.charger_id, charger.connector_id)
            store.add_log(log_key, f"< {json.dumps(message)}", log_type="charger")
            store.register_pending_call(
                message_id,
                {
                    "action": action,
                    "charger_id": charger.charger_id,
                    "connector_id": charger.connector_id,
                    "log_key": log_key,
                    "forwarded": True,
                    "requested_at": timezone.now(),
                },
            )
            try:
                async_to_sync(ws.send)(json.dumps(message))
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning(
                    "Forwarded command %s failed for charger %s: %s",
                    action,
                    charger.charger_id,
                    exc,
                )
                error = json.dumps(
                    [
                        4,
                        message_id,
                        "InternalError",
                        "Failed to forward command.",
                        {},
                    ]
                )
                try:
                    session.connection.send(error)
                except Exception:
                    pass
                continue

            session.pending_call_ids.add(message_id)

    def ensure_keepalive_task(self, *, idle_seconds: int = 60) -> None:
        """Ensure the keepalive loop runs in the current asyncio process."""

        if idle_seconds <= 0:
            return
        existing = self._keepalive_task
        if existing is not None and not existing.done():
            if self._keepalive_interval == idle_seconds:
                return
            existing.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._keepalive_interval = idle_seconds
        self._keepalive_task = loop.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Run periodic keepalive checks on forwarding sessions."""

        while True:
            interval = self._keepalive_interval or 0
            if interval <= 0:
                return
            await asyncio.sleep(interval)
            await asyncio.to_thread(
                self.keepalive_sessions, idle_seconds=interval
            )

    def active_target_ids(self, only_connected: bool = True) -> set[int]:
        """Return the set of target node IDs with active sessions."""

        ids: set[int] = set()
        with self._sync_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.node_id is None:
                continue
            if not only_connected or session.is_connected:
                ids.add(session.node_id)
        return ids

    def is_target_active(self, target_id: int | None) -> bool:
        """Return ``True`` when a connected session targets ``target_id``."""

        if target_id is None:
            return False
        return target_id in self.active_target_ids(only_connected=True)

    def sync_forwarded_charge_points(self, *, refresh_forwarders: bool = True) -> int:
        """Ensure websocket connections exist for forwarded charge points."""

        from apps.nodes.models import Node
        from apps.ocpp.models import CPForwarder
        from ..models import Charger

        local = Node.get_local()
        if not local:
            self.prune_inactive_sessions(set())
            CPForwarder.objects.update_running_state(set())
            return 0

        if refresh_forwarders:
            CPForwarder.objects.sync_forwarding_targets()
        forwarders_by_target = {
            forwarder.target_node_id: forwarder
            for forwarder in CPForwarder.objects.filter(enabled=True)
        }

        chargers_qs = (
            Charger.objects.filter(export_transactions=True, forwarded_to__isnull=False)
            .select_related("forwarded_to", "node_origin")
            .order_by("pk")
        )

        node_filter = Q(node_origin__isnull=True)
        if local.pk:
            node_filter |= Q(node_origin=local)

        chargers = list(chargers_qs.filter(node_filter))
        active_ids = {charger.pk for charger in chargers}

        self.prune_inactive_sessions(active_ids)

        if not chargers:
            CPForwarder.objects.update_running_state(set())
            return 0

        connected = 0

        for charger in chargers:
            target = charger.forwarded_to
            forwarder = forwarders_by_target.get(getattr(target, "pk", None))
            if not target:
                continue
            if local.pk and getattr(target, "pk", None) == local.pk:
                continue

            existing = self.get_session(charger.pk)
            if existing and existing.node_id == getattr(target, "pk", None):
                if forwarder:
                    existing.forwarder_id = getattr(forwarder, "pk", None)
                    existing.forwarded_messages = tuple(
                        forwarder.get_forwarded_messages()
                    )
                    existing.forwarded_calls = tuple(forwarder.get_forwarded_calls())
                else:
                    existing.forwarder_id = None
                    existing.forwarded_messages = None
                    existing.forwarded_calls = None
                if existing.is_connected:
                    continue
                self.remove_session(charger.pk)

            session = self.connect_forwarding_session(charger, target)
            if session is None:
                logger.warning(
                    "Unable to establish forwarding websocket for charger %s",
                    getattr(charger, "charger_id", charger.pk),
                )
                continue

            Charger.objects.filter(pk=charger.pk).update(
                forwarding_watermark=session.connected_at
            )
            if forwarder:
                session.forwarder_id = getattr(forwarder, "pk", None)
                session.forwarded_messages = tuple(
                    forwarder.get_forwarded_messages()
                )
                session.forwarded_calls = tuple(forwarder.get_forwarded_calls())
                forwarder.mark_running(session.connected_at)
            connected += 1

        CPForwarder.objects.update_running_state(self.active_target_ids())

        return connected


forwarder = Forwarder()

__all__ = [
    "Forwarder",
    "ForwardingSession",
    "forwarder",
]
