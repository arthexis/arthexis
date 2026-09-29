"""Join durable discovery capture evidence to the normal OCPP domain path."""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.ocpp.discovery.projection import project_discovery_event
from apps.ocpp.discovery.session import DiscoverySession
from apps.ocpp.models import Charger

logger = logging.getLogger(__name__)


def _discovery_root() -> Path:
    return Path(settings.DATA_DIR)


def _open_session(session_id: str) -> DiscoverySession:
    return DiscoverySession.open(
        _discovery_root(),
        session_id,
        projector=project_discovery_event,
    )


def _sessions_newest_first() -> list[DiscoverySession]:
    return [
        DiscoverySession.open(
            _discovery_root(),
            str(item["session_id"]),
            projector=project_discovery_event,
        )
        for item in reversed(DiscoverySession.list(_discovery_root()))
    ]


def arm_discovery_handoff(
    session_id: str,
    *,
    charger_identity: str | None = None,
    client_host: str | None = None,
    original_destination: Mapping[str, Any] | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Arm one unified OCPP discovery session before redirect mutation."""
    session = _open_session(session_id)
    return session.arm_handoff(
        charger_identity=charger_identity,
        client_host=client_host,
        original_destination=original_destination,
        strategy=strategy,
    )


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
