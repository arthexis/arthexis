import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from django.db import close_old_connections
from django.utils import timezone

from apps.nodes.models import NodeFeature
from .utils import capture_rpi_snapshot
from apps.content.utils import save_screenshot

logger = logging.getLogger(__name__)


def is_rpi_camera_feature_active() -> bool:
    """Return ``True`` if the Raspberry Pi camera feature is active."""

    try:
        feature = NodeFeature.objects.filter(slug="rpi-camera").first()
    except Exception:  # pragma: no cover - database may be unavailable early
        logger.debug(
            "RFID snapshot skipped: unable to query node features", exc_info=True
        )
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


def _decode_qr_payload(image: Path, *, timeout: int = 8) -> str | None:
    """Return the first decoded QR payload from *image* or ``None``."""

    tool_path = shutil.which("zbarimg")
    if not tool_path:
        logger.debug("QR decode skipped: zbarimg is unavailable")
        return None

    try:
        result = subprocess.run(
            [tool_path, "--raw", "--quiet", str(image)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.warning("QR decode failed: %s", exc)
        return None

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if message:
            logger.debug("QR decode returned %s: %s", result.returncode, message)
        return None

    output = (result.stdout or result.stderr or "").strip()
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.upper().startswith("QR-CODE:"):
            candidate = candidate.split(":", 1)[-1].strip()
        if candidate:
            return candidate
    return None


def scan_camera_qr(*, endianness: str | None = None) -> dict[str, Any]:
    """Capture a QR code using the Raspberry Pi camera when enabled."""

    if not is_rpi_camera_feature_active():
        return {"rfid": None, "label_id": None}

    snapshot: Path | None = None
    try:
        snapshot = capture_rpi_snapshot()
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.warning("Camera scan failed: %s", exc)
        return {"error": str(exc)}

    try:
        payload = _decode_qr_payload(snapshot)
    finally:
        if snapshot is not None:
            try:
                snapshot.unlink()
            except Exception:  # pragma: no cover - best effort cleanup
                logger.debug("Unable to delete camera snapshot %s", snapshot)

    if not payload:
        return {"rfid": None, "label_id": None}

    try:
        from apps.cards.reader import validate_rfid_value
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unable to validate QR payload")
        return {"error": str(exc)}

    result = validate_rfid_value(payload, kind="QR", endianness=endianness)
    result.setdefault("source", "camera")
    return result


def queue_camera_snapshot(rfid: str, payload: dict[str, Any] | None = None) -> None:
    """Queue a Raspberry Pi snapshot when the camera feature is enabled."""

    if not rfid:
        return
    if not is_rpi_camera_feature_active():
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


__all__ = [
    "is_rpi_camera_feature_active",
    "queue_camera_snapshot",
    "scan_camera_qr",
]
