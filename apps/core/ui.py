"""UI environment detection helpers shared across subsystems."""

from __future__ import annotations

import os
import sys


def has_graphical_display() -> bool:
    """Return whether the current process can open graphical UI windows."""

    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

