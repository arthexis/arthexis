"""RFID hardware detection helpers used by installation scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple


def _ensure_django() -> None:
    """Configure Django so detection utilities can import project modules."""
    try:
        from django.conf import settings
    except Exception as exc:  # pragma: no cover - django missing entirely
        raise RuntimeError("Django is required for RFID detection") from exc

    if getattr(settings, "configured", False):
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def _lockfile_status() -> Tuple[bool, Path | None]:
    """Return whether a scanner lock file exists and its path when available."""

    try:
        from .background_reader import lock_file_active
    except Exception:  # pragma: no cover - import edge cases
        return False, None

    try:
        active, lock = lock_file_active()
    except Exception:  # pragma: no cover - settings misconfiguration
        return False, None

    return active, lock


def _assume_detected(reason: str | None, lock: Path | None) -> Dict[str, Any]:
    """Return metadata indicating detection succeeded via lock file."""

    response: Dict[str, Any] = {"detected": True, "assumed": True}
    if reason:
        response["reason"] = reason
    if lock is not None:
        response["lockfile"] = lock.as_posix()
    return response


def detect_scanner() -> Dict[str, Any]:
    """Return detection metadata for the RFID scanner."""
    try:
        _ensure_django()
    except Exception as exc:
        return {"detected": False, "reason": str(exc)}

    has_lock, lock_path = _lockfile_status()

    try:
        from .irq_wiring_check import check_irq_pin
    except Exception as exc:  # pragma: no cover - unexpected import error
        if has_lock:
            return _assume_detected(str(exc), lock_path)
        return {"detected": False, "reason": str(exc)}

    result = check_irq_pin()
    if result.get("error"):
        if has_lock:
            return _assume_detected(result.get("error"), lock_path)
        return {"detected": False, "reason": result["error"]}

    response: Dict[str, Any] = {"detected": True}
    if "irq_pin" in result:
        response["irq_pin"] = result.get("irq_pin")

    if result.get("busy"):
        response["assumed"] = True
        response["busy"] = True
        reason = result.get("reason") or "RFID scanner busy"
        if reason:
            response["reason"] = reason
        if "errno" in result and result["errno"] is not None:
            response["errno"] = result["errno"]

    if has_lock and lock_path is not None:
        response["lockfile"] = str(lock_path)
    return response


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m apps.cards.detect``."""
    result = detect_scanner()
    if result.get("detected"):
        if result.get("assumed"):
            lockfile = result.get("lockfile")
            reason = result.get("reason")
            if lockfile and reason:
                print(
                    f"RFID scanner assumed active via lock file {lockfile} "
                    f"(detection failed: {reason})"
                )
            elif lockfile:
                print(f"RFID scanner assumed active via lock file {lockfile}")
            elif reason:
                print(f"RFID scanner assumed active (detection failed: {reason})")
            else:  # pragma: no cover - defensive default
                print("RFID scanner assumed active based on previous usage")
            return 0
        irq_pin = result.get("irq_pin")
        if irq_pin is None:
            print("RFID scanner detected (IRQ pin undetermined)")
        else:
            print(f"RFID scanner detected (IRQ pin {irq_pin})")
        return 0

    reason = result.get("reason")
    if reason:
        print(f"RFID scanner not detected: {reason}")
    else:  # pragma: no cover - defensive default
        print("RFID scanner not detected")
    return 1


if __name__ == "__main__":
    sys.exit(main())
