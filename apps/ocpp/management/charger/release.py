"""Manually release a discovery session's owned network redirect."""

from pathlib import Path

from django.conf import settings

from apps.ocpp.discovery.capture import release_capture
from apps.ocpp.discovery.session import DiscoverySession


def release(
    session_id: str,
    *,
    root: str | None = None,
) -> dict[str, object]:
    """Release the temporary Gway redirect owned by one discovery session."""

    data_root = Path(root) if root else Path(settings.DATA_DIR)
    session = DiscoverySession.open(data_root, session_id)
    result = release_capture(session)
    summary = session.write_summary()
    return {**summary, "capture_action": result}
