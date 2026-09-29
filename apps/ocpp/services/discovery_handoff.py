"""Join durable discovery capture evidence to the normal OCPP domain path."""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.ocpp.discovery.session import DiscoverySession
from apps.events.services import publish_safely
from apps.ocpp.models import Charger

logger = logging.getLogger(__name__)


def _project_discovery_event(event: Mapping[str, Any]) -> object:
    """Best-effort projection of one already-durable discovery event."""
    payload = {
        "session_id": event["session_id"],
        "sequence": event["sequence"],
        "event_type": event["event_type"],
        "category": event["category"],
        "artifact_refs": list(event.get("artifact_refs") or []),
    }
    return publish_safely(
        event_type="discovery.event",
        producer="arthexis.ocpp.discovery",
        payload=payload,
    )


def _discovery_root() -> Path:
    return Path(settings.DATA_DIR)


def _open_session(session_id: str) -> DiscoverySession:
    return DiscoverySession.open(_discovery_root(), session_id)


def _sessions_newest_first() -> list[DiscoverySession]:
    return [
        DiscoverySession.open(_discovery_root(), str(item["session_id"]))
        for item in reversed(DiscoverySession.list(_discovery_root()))
    ]


def _client_host(scope: Mapping[str, object]) -> str | None:
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return None
    host = client[0]
    return host if isinstance(host, str) else None


def claim_and_record_discovery_handoff(
    *,
    charger: Charger,
    scope: Mapping[str, object],
    protocol: str,
    offered_subprotocols: list[str],
) -> DiscoverySession | None:
    """Link a redirected connection to discovery without becoming its transport owner."""
    client_host = _client_host(scope)
    session = next(
        (
            candidate
            for candidate in _sessions_newest_first()
            if candidate.claim_handoff(
                charger_identity=charger.identity,
                client_host=client_host,
            )
            is not None
        ),
        None,
    )
    if session is None:
        return None

    try:
        session.record(
            "redirect_observed",
            metadata={
                "client_host": client_host,
                "path": scope.get("path", ""),
            },
        )
        session.record(
            "ocpp_connection",
            metadata={
                "charger": {
                    "id": charger.pk,
                    "identity": charger.identity,
                },
                "protocol": protocol,
                "offered_subprotocols": list(offered_subprotocols),
                "path": scope.get("path", ""),
                "client_host": client_host,
            },
        )
        session.record(
            "capture_succeeded",
            metadata={
                "charger_id": charger.pk,
                "charger_identity": charger.identity,
                "protocol": protocol,
            },
        )
        session.write_summary()
    except Exception:
        logger.exception(
            "Discovery handoff %s was claimed but evidence finalization failed.",
            session.session_id,
        )
    return session
