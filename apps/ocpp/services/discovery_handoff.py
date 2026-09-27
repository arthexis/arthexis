"""Join durable discovery capture evidence to the normal OCPP domain path."""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.conf import settings

from arthexis.discovery import DiscoverySession, DiscoveryStore
from apps.ocpp.models import Charger

logger = logging.getLogger(__name__)


def discovery_store() -> DiscoveryStore:
    """Return the filesystem-backed discovery store for this Arthexis instance."""
    return DiscoveryStore(Path(settings.DATA_DIR) / "discovery")


def arm_discovery_handoff(
    session_id: str,
    *,
    charger_identity: str | None = None,
    client_host: str | None = None,
    original_destination: Mapping[str, Any] | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Arm one discovery session before Gway applies a capture redirect."""
    session = discovery_store().open(session_id)
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
    store = discovery_store()
    client_host = _client_host(scope)
    session = store.claim_handoff(
        charger_identity=charger.identity,
        client_host=client_host,
    )
    if session is None:
        return None

    try:
        session.append(
            "redirect_observed",
            data={
                "client_host": client_host,
                "path": scope.get("path", ""),
            },
        )
        session.append(
            "ocpp_connection",
            data={
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
        session.append(
            "capture_succeeded",
            data={
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
