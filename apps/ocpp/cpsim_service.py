from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone


CPSIM_FEATURE_SLUG = "cpsim-service"
CPSIM_REQUEST_LOCK_NAME = "cpsim-service.lck"


def _serialize_params(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if is_dataclass(params):
        return asdict(params)
    if isinstance(params, dict):
        return dict(params)
    return {"value": params}


def _lock_path(*, base_dir: Path | None = None) -> Path:
    base = Path(base_dir or settings.BASE_DIR)
    lock_dir = base / ".locks"
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
    lock_path = _lock_path(base_dir=base_dir)
    lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return lock_path


def cpsim_service_enabled() -> bool:
    try:
        from apps.nodes.models import NodeFeature
    except Exception:
        return False
    feature = NodeFeature.objects.filter(slug=CPSIM_FEATURE_SLUG).first()
    return bool(feature and feature.is_enabled)


def get_cpsim_feature():
    try:
        from apps.nodes.models import NodeFeature
    except Exception:
        return None
    return NodeFeature.objects.filter(slug=CPSIM_FEATURE_SLUG).first()
