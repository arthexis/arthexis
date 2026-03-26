from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


RFID_SCANNER_SLUG = "rfid-scanner"
RFID_LOCK_NAME = "rfid.lck"


def _lock_paths(
    *,
    node: Node | None,
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> list[Path]:
    """Return lock-file locations that can represent scanner availability."""

    lock_paths: list[Path] = []
    resolved_base_dir = base_dir or Path(settings.BASE_DIR)
    lock_paths.append(resolved_base_dir / ".locks" / RFID_LOCK_NAME)
    if node is not None:
        resolved_base_path = base_path or node.get_base_path()
        node_lock = resolved_base_path / ".locks" / RFID_LOCK_NAME
        if node_lock not in lock_paths:
            lock_paths.append(node_lock)
    return lock_paths


def _lockfile_status(
    *,
    node: Node | None,
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> tuple[bool, Path | None]:
    """Return whether a compatibility RFID lock file already exists."""

    try:
        from apps.cards.background_reader import lock_file_active
    except Exception:  # pragma: no cover - defensive import fallback
        lock_file_active = None

    default_base_dir = Path(settings.BASE_DIR)
    resolved_base_dir = base_dir or default_base_dir

    for path in _lock_paths(node=node, base_dir=resolved_base_dir, base_path=base_path):
        try:
            if not path.exists():
                continue
        except OSError:
            continue

        if (
            lock_file_active is not None
            and resolved_base_dir == default_base_dir
            and path == resolved_base_dir / ".locks" / RFID_LOCK_NAME
        ):
            try:
                is_active, active_path = lock_file_active()
            except Exception:
                continue
            if is_active:
                return True, active_path
            continue

        return True, path
    return False, None


def _assume_detected(reason: str | None, lock: Path | None) -> dict[str, Any]:
    """Build a success payload when prior scanner activity was recorded."""

    payload: dict[str, Any] = {"detected": True, "assumed": True}
    if reason:
        payload["reason"] = reason
    if lock is not None:
        payload["lockfile"] = lock.as_posix()
    return payload


def _service_detection(*, base_dir: Path | None = None) -> dict[str, Any]:
    """Probe the RFID service fallback used on systemd-based installations."""

    try:
        from apps.cards.rfid_service import rfid_service_enabled, service_available
    except Exception as exc:  # pragma: no cover - unexpected import errors
        return {"detected": False, "reason": str(exc)}

    resolved_base_dir = base_dir or Path(settings.BASE_DIR)
    lock_dir = resolved_base_dir / ".locks"
    if rfid_service_enabled(lock_dir=lock_dir):
        return {"detected": True, "assumed": True, "reason": "RFID service enabled"}
    try:
        if service_available():
            return {"detected": True, "assumed": True, "reason": "RFID service available"}
    except Exception as exc:  # pragma: no cover - hardware/runtime dependent
        return {"detected": False, "reason": str(exc)}
    return {"detected": False, "reason": "RFID scanner not detected"}


def detect_scanner_capability(
    *,
    node: Node | None = None,
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> dict[str, Any]:
    """Return detection metadata for the RFID scanner node feature."""

    has_lock, lock_path = _lockfile_status(
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )
    if has_lock:
        return _assume_detected(None, lock_path)

    try:
        from apps.cards.irq_wiring_check import check_irq_pin
    except Exception as exc:  # pragma: no cover - import edge cases
        irq_result = {"error": str(exc)}
    else:
        irq_result = check_irq_pin()

    if not irq_result.get("error"):
        payload: dict[str, Any] = {"detected": True}
        if "irq_pin" in irq_result:
            payload["irq_pin"] = irq_result.get("irq_pin")
        if irq_result.get("busy"):
            payload["assumed"] = True
            payload["busy"] = True
            reason = irq_result.get("reason") or "RFID scanner busy"
            if reason:
                payload["reason"] = reason
            if "errno" in irq_result and irq_result["errno"] is not None:
                payload["errno"] = irq_result["errno"]
        return payload

    service_result = _service_detection(base_dir=base_dir)
    if service_result.get("detected"):
        return service_result

    return {"detected": False, "reason": irq_result.get("error") or service_result.get("reason")}


def check_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Return feature eligibility for the RFID scanner hook."""

    if slug != RFID_SCANNER_SLUG:
        return None
    return bool(
        detect_scanner_capability(
            node=node,
            base_dir=base_dir,
            base_path=base_path,
        ).get("detected")
    )


def _write_compatibility_lock(
    *,
    node: Node,
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> None:
    """Persist lock files for compatibility with existing runtime checks."""

    resolved_base_dir = base_dir or Path(settings.BASE_DIR)

    for path in _lock_paths(node=node, base_dir=resolved_base_dir, base_path=base_path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            continue


def setup_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Perform RFID setup work and report whether the feature is available."""

    if slug != RFID_SCANNER_SLUG:
        return None

    detected = bool(
        detect_scanner_capability(
            node=node,
            base_dir=base_dir,
            base_path=base_path,
        ).get("detected")
    )
    if detected:
        _write_compatibility_lock(
            node=node,
            base_dir=base_dir,
            base_path=base_path,
        )
    return detected


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register card app feature auto-detection callbacks."""

    registry.register(
        RFID_SCANNER_SLUG,
        check=check_node_feature,
        setup=setup_node_feature,
    )


__all__ = [
    "check_node_feature",
    "detect_scanner_capability",
    "register_node_feature_detection",
    "setup_node_feature",
]
