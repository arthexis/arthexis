from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


RFID_SCANNER_SLUG = "rfid-scanner"
RFID_LOCK_NAME = "rfid.lck"


def _lock_paths(*, node: "Node" | None) -> list[Path]:
    """Return lock-file locations that can represent scanner availability."""

    lock_paths: list[Path] = []
    base_dir = Path(settings.BASE_DIR)
    lock_paths.append(base_dir / ".locks" / RFID_LOCK_NAME)
    if node is not None:
        node_lock = node.get_base_path() / ".locks" / RFID_LOCK_NAME
        if node_lock not in lock_paths:
            lock_paths.append(node_lock)
    return lock_paths


def _lockfile_status(*, node: "Node" | None) -> tuple[bool, Path | None]:
    """Return whether a compatibility RFID lock file already exists."""

    for path in _lock_paths(node=node):
        try:
            if path.exists():
                return True, path
        except OSError:
            continue
    return False, None


def _assume_detected(reason: str | None, lock: Path | None) -> dict[str, Any]:
    """Build a success payload when prior scanner activity was recorded."""

    payload: dict[str, Any] = {"detected": True, "assumed": True}
    if reason:
        payload["reason"] = reason
    if lock is not None:
        payload["lockfile"] = lock.as_posix()
    return payload


def _service_detection() -> dict[str, Any]:
    """Probe the RFID service fallback used on systemd-based installations."""

    try:
        from apps.cards.rfid_service import rfid_service_enabled, service_available
    except Exception as exc:  # pragma: no cover - unexpected import errors
        return {"detected": False, "reason": str(exc)}

    lock_dir = Path(settings.BASE_DIR) / ".locks"
    if rfid_service_enabled(lock_dir=lock_dir):
        return {"detected": True, "assumed": True, "reason": "RFID service enabled"}
    try:
        if service_available():
            return {"detected": True, "assumed": True, "reason": "RFID service available"}
    except Exception as exc:  # pragma: no cover - hardware/runtime dependent
        return {"detected": False, "reason": str(exc)}
    return {"detected": False, "reason": "RFID scanner not detected"}


def detect_scanner_capability(*, node: "Node" | None = None) -> dict[str, Any]:
    """Return detection metadata for the RFID scanner node feature."""

    has_lock, lock_path = _lockfile_status(node=node)
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

    service_result = _service_detection()
    if service_result.get("detected"):
        if lock_path is not None:
            service_result.setdefault("lockfile", str(lock_path))
        return service_result

    if has_lock:
        return _assume_detected(irq_result.get("error"), lock_path)
    return {"detected": False, "reason": irq_result.get("error") or service_result.get("reason")}


def check_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Return feature eligibility for the RFID scanner hook."""

    if slug != RFID_SCANNER_SLUG:
        return None
    return bool(detect_scanner_capability(node=node).get("detected"))


def _write_compatibility_lock(*, node: "Node") -> None:
    """Persist lock files for compatibility with existing runtime checks."""

    for path in _lock_paths(node=node):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            continue


def setup_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Perform RFID setup work and report whether the feature is available."""

    if slug != RFID_SCANNER_SLUG:
        return None

    detected = bool(detect_scanner_capability(node=node).get("detected"))
    if detected:
        _write_compatibility_lock(node=node)
    return detected


__all__ = [
    "check_node_feature",
    "detect_scanner_capability",
    "setup_node_feature",
]
