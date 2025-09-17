"""RFID hardware detection helpers used by installation scripts."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict


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


def detect_scanner() -> Dict[str, Any]:
    """Return detection metadata for the RFID scanner."""
    try:
        _ensure_django()
    except Exception as exc:
        return {"detected": False, "reason": str(exc)}

    try:
        from .irq_wiring_check import check_irq_pin
    except Exception as exc:  # pragma: no cover - unexpected import error
        return {"detected": False, "reason": str(exc)}

    result = check_irq_pin()
    if result.get("error"):
        return {"detected": False, "reason": result["error"]}

    return {"detected": True, "irq_pin": result.get("irq_pin")}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m ocpp.rfid.detect``."""
    result = detect_scanner()
    if result.get("detected"):
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
