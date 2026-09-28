"""Manually activate the optional Gway redirect for a discovery session."""

from pathlib import Path

from django.conf import settings

from apps.ocpp.discovery.capture import start_capture
from apps.ocpp.discovery.session import DiscoverySession


def capture(
    session_id: str,
    *,
    root: str | None = None,
    interface: str | None = None,
) -> dict[str, object]:
    """Activate the persisted discovery candidate redirect for one session."""

    data_root = Path(root) if root else Path(settings.DATA_DIR)
    session = DiscoverySession.open(data_root, session_id)
    selected_interface = interface
    if selected_interface is None:
        selected_interface = next(
            (
                str(event["metadata"]["interface"])
                for event in session.events()
                if event["event_type"] == "interface_selected"
                and isinstance(event.get("metadata"), dict)
                and event["metadata"].get("interface")
            ),
            None,
        )
    if not selected_interface:
        raise ValueError("Discovery session has no charger-facing interface")

    result = start_capture(session, interface=selected_interface)
    summary = session.write_summary()
    return {**summary, "capture_action": result}
