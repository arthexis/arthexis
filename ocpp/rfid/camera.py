from __future__ import annotations

import json
import logging
import threading
from typing import Any

from django.db import close_old_connections
from django.utils import timezone

from nodes.models import NodeFeature
from nodes.utils import capture_rpi_snapshot, save_screenshot

logger = logging.getLogger(__name__)


def _camera_feature_enabled() -> bool:
    """Return ``True`` if the Raspberry Pi camera feature is active."""

    try:
        feature = NodeFeature.objects.filter(slug="rpi-camera").first()
    except Exception:  # pragma: no cover - database may be unavailable early
        logger.debug("RFID snapshot skipped: unable to query node features", exc_info=True)
        return False
    if not feature:
        return False
    try:
        return bool(feature.is_enabled)
    except Exception:  # pragma: no cover - defensive guard
        logger.debug("RFID snapshot skipped: feature state unavailable", exc_info=True)
        return False


def _serialize_metadata(metadata: dict[str, Any]) -> str:
    """Convert *metadata* into a JSON string suitable for storage."""

    try:
        return json.dumps(metadata, sort_keys=True, default=str)
    except Exception:  # pragma: no cover - defensive guard
        fallback = {key: str(value) for key, value in metadata.items()}
        return json.dumps(fallback, sort_keys=True)


def _capture_snapshot_worker(metadata: dict[str, Any]) -> None:
    """Background worker that captures and stores a camera snapshot."""

    close_old_connections()
    try:
        path = capture_rpi_snapshot()
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.warning("RFID snapshot capture failed: %s", exc)
        close_old_connections()
        return

    content = _serialize_metadata(metadata)
    try:
        save_screenshot(path, method="RFID_SCAN", content=content)
    except Exception:  # pragma: no cover - database or filesystem issues
        logger.exception("RFID snapshot storage failed")
    finally:
        close_old_connections()


def queue_camera_snapshot(rfid: str, payload: dict[str, Any] | None = None) -> None:
    """Queue a Raspberry Pi snapshot when the camera feature is enabled."""

    if not rfid:
        return
    if not _camera_feature_enabled():
        return

    metadata: dict[str, Any] = dict(payload or {})
    metadata.setdefault("source", "rfid-scan")
    metadata.setdefault("captured_at", timezone.now().isoformat())
    metadata["rfid"] = rfid

    thread = threading.Thread(
        target=_capture_snapshot_worker,
        name="rfid-camera-snapshot",
        args=(metadata,),
        daemon=True,
    )
    thread.start()
