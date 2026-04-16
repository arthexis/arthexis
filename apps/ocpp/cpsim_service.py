from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.features.utils import is_suite_feature_enabled


CPSIM_FEATURE_SLUG = "ocpp-simulator"
CPSIM_REQUEST_LOCK_NAME = "cpsim-service.lck"
CPSIM_START_QUEUED_STATUS = "cpsim-service start queued (awaiting worker)"
CPSIM_STOP_QUEUED_STATUS = "cpsim-service stop queued (awaiting worker)"


def _serialize_params(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if is_dataclass(params):
        return asdict(params)
    if isinstance(params, dict):
        return dict(params)
    return {"value": params}


def _lock_path(*, base_dir: Path | None = None, ensure_dir: bool = True) -> Path:
    base = Path(base_dir or settings.BASE_DIR)
    lock_dir = base / ".locks"
    if ensure_dir:
        lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / CPSIM_REQUEST_LOCK_NAME


def queue_cpsim_request(
    *,
    action: str,
    params: Any = None,
    slot: int | None = None,
    simulator_id: int | None = None,
    name: str | None = None,
    source: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    payload = {
        "action": action,
        "requested_at": timezone.now().isoformat(),
        "slot": slot,
        "simulator_id": simulator_id,
        "name": name,
        "source": source,
        "params": _serialize_params(params),
    }
    lock_path = _lock_path(base_dir=base_dir, ensure_dir=True)
    lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return lock_path


def queue_cpsim_service_toggle(
    *,
    enabled: bool,
    source: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    action = "service-enable" if enabled else "service-disable"
    return queue_cpsim_request(
        action=action,
        source=source,
        base_dir=base_dir,
    )


def cpsim_service_enabled() -> bool:
    """Return whether the OCPP Simulator suite feature is enabled."""

    return is_suite_feature_enabled(CPSIM_FEATURE_SLUG, default=False)


def get_cpsim_feature():
    """Return the OCPP Simulator suite feature definition if configured."""

    try:
        from apps.features.models import Feature
    except (ImportError, RuntimeError):
        return None
    return Feature.objects.filter(slug=CPSIM_FEATURE_SLUG).first()


def get_cpsim_request_metadata(*, base_dir: Path | None = None) -> dict[str, Any]:
    """Return lock-file metadata for the queued cpsim service request."""

    lock_path = _lock_path(base_dir=base_dir, ensure_dir=False)
    try:
        if not lock_path.exists():
            return {"queued": False, "lock_path": str(lock_path)}
    except OSError:
        return {"queued": False, "lock_path": str(lock_path)}

    queued_at = timezone.now()
    try:
        queued_at = datetime.fromtimestamp(
            lock_path.stat().st_mtime,
            tz=dt_timezone.utc,
        )
    except (FileNotFoundError, OSError, ValueError):
        pass

    age_seconds = max((timezone.now() - queued_at).total_seconds(), 0.0)
    return {
        "queued": True,
        "lock_path": str(lock_path),
        "queued_at": queued_at,
        "age_seconds": age_seconds,
    }


def is_cpsim_start_queued_status(status: str | None) -> bool:
    """Return whether simulator state reflects a cpsim start queue request."""

    return str(status or "").startswith("cpsim-service start queued")
