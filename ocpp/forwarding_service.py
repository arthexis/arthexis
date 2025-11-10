"""Utilities for managing OCPP charge point forwarding sessions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.connection, "connected", False))


_FORWARDING_SESSIONS: MutableMapping[int, ForwardingSession] = {}


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
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        for prefix in ("", "/ws"):
            path = f"{base_path}{prefix}/{encoded_id}".replace("//", "/")
            if not path.startswith("/"):
                path = f"/{path}"
            urls.append(urlunsplit((scheme, parsed.netloc, path, "", "")))
    return iter(urls)


def _close_forwarding_session(session: ForwardingSession) -> None:
    """Close the websocket connection associated with ``session`` if open."""

    connection = session.connection
    if connection is None:
        return
    try:
        connection.close()
    except Exception:  # pragma: no cover - best effort close
        pass


def get_session(charger_pk: int) -> ForwardingSession | None:
    """Return the forwarding session for ``charger_pk`` when present."""

    return _FORWARDING_SESSIONS.get(charger_pk)


def iter_sessions() -> Iterator[ForwardingSession]:
    """Yield active forwarding sessions."""

    return iter(_FORWARDING_SESSIONS.values())


def clear_sessions() -> None:
    """Close and drop all active forwarding sessions."""

    for session in list(_FORWARDING_SESSIONS.values()):
        _close_forwarding_session(session)
    _FORWARDING_SESSIONS.clear()


def remove_session(charger_pk: int) -> None:
    """Close and remove the session for ``charger_pk`` when it exists."""

    session = _FORWARDING_SESSIONS.pop(charger_pk, None)
    if session is not None:
        _close_forwarding_session(session)


def prune_inactive_sessions(active_ids: Iterable[int]) -> None:
    """Close sessions that no longer map to a charger in ``active_ids``."""

    valid = set(active_ids)
    for pk in list(_FORWARDING_SESSIONS.keys()):
        if pk not in valid:
            remove_session(pk)


def connect_forwarding_session(charger, target_node, *, timeout: float = 5.0) -> ForwardingSession | None:
    """Establish a websocket forwarding session for ``charger``.

    Returns the created session or ``None`` when all connection attempts fail.
    """

    if getattr(charger, "pk", None) is None:
        return None

    for url in _candidate_forwarding_urls(target_node, charger):
        try:
            connection = create_connection(
                url,
                timeout=timeout,
                subprotocols=["ocpp1.6"],
            )
        except (WebSocketException, OSError) as exc:
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
        )
        _FORWARDING_SESSIONS[charger.pk] = session
        logger.info(
            "Established forwarding websocket for charger %s to %s via %s",
            getattr(charger, "charger_id", charger.pk),
            target_node,
            url,
        )
        return session

    return None


def active_target_ids(only_connected: bool = True) -> set[int]:
    """Return the set of target node IDs with active sessions."""

    ids: set[int] = set()
    for session in _FORWARDING_SESSIONS.values():
        if session.node_id is None:
            continue
        if not only_connected or session.is_connected:
            ids.add(session.node_id)
    return ids


def is_target_active(target_id: int | None) -> bool:
    """Return ``True`` when a connected session targets ``target_id``."""

    if target_id is None:
        return False
    return target_id in active_target_ids(only_connected=True)


def sync_forwarded_charge_points(*, refresh_forwarders: bool = True) -> int:
    """Ensure websocket connections exist for forwarded charge points."""

    from nodes.models import Node
    from protocols.models import CPForwarder
    from .models import Charger

    local = Node.get_local()
    if not local:
        prune_inactive_sessions(set())
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

    prune_inactive_sessions(active_ids)

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

        existing = get_session(charger.pk)
        if existing and existing.node_id == getattr(target, "pk", None):
            if forwarder:
                existing.forwarder_id = getattr(forwarder, "pk", None)
                existing.forwarded_messages = tuple(
                    forwarder.get_forwarded_messages()
                )
            else:
                existing.forwarder_id = None
                existing.forwarded_messages = None
            if existing.is_connected:
                continue
            remove_session(charger.pk)

        session = connect_forwarding_session(charger, target)
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
            forwarder.mark_running(session.connected_at)
        connected += 1

    CPForwarder.objects.update_running_state(active_target_ids())

    return connected


__all__ = [
    "ForwardingSession",
    "active_target_ids",
    "clear_sessions",
    "connect_forwarding_session",
    "get_session",
    "is_target_active",
    "iter_sessions",
    "prune_inactive_sessions",
    "remove_session",
    "sync_forwarded_charge_points",
]
