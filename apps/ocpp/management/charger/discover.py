"""Foreground passive charger discovery operation."""

from pathlib import Path

from django.conf import settings

from apps.ocpp.discovery.network import TsharkObserver, run_passive_discovery
from apps.ocpp.discovery.render import render_discovery_events


def discover(
    interface: str,
    role: str | None = None,
    root: str | None = None,
) -> dict[str, object]:
    """Observe a charger-facing interface without transmitting or mutating networking."""

    session = run_passive_discovery(
        interface=interface,
        role=role,
        root=Path(root) if root else Path(settings.DATA_DIR),
        observer=TsharkObserver(),
    )
    summary = session.write_summary()
    return {
        **summary,
        "display": render_discovery_events(session.session_id, session.events()),
    }
