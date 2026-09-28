"""Foreground passive charger discovery operation."""

from pathlib import Path

from django.conf import settings

from apps.ocpp.discovery.capture import start_capture
from apps.ocpp.discovery.network import TsharkObserver, run_passive_discovery
from apps.ocpp.discovery.render import render_discovery_events


def discover(
    interface: str,
    role: str | None = None,
    root: str | None = None,
    capture: bool = False,
) -> dict[str, object]:
    """Observe a charger-facing interface and optionally prepare automatic capture."""

    session = run_passive_discovery(
        interface=interface,
        role=role,
        root=Path(root) if root else Path(settings.DATA_DIR),
        observer=TsharkObserver(),
    )
    if capture:
        start_capture(session, interface=interface)

    summary = session.write_summary()
    return {
        **summary,
        "display": render_discovery_events(session.session_id, session.events()),
    }
