"""Best-effort live projection for already-durable OCPP discovery events."""

from collections.abc import Mapping
from typing import Any

from apps.events.services import publish_safely


def project_discovery_event(event: Mapping[str, Any]) -> object:
    """Project a compact reference to one already-persisted discovery event."""
    return publish_safely(
        event_type="discovery.event",
        producer="arthexis.ocpp.discovery",
        payload={
            "session_id": event["session_id"],
            "sequence": event["sequence"],
            "event_type": event["event_type"],
            "category": event["category"],
            "artifact_refs": list(event.get("artifact_refs") or []),
        },
    )
