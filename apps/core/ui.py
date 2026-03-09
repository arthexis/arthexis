"""UI environment detection helpers shared across subsystems."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sys


GRAPHICAL_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
)
WSL_PROC_VERSION_PATH = Path("/proc/version")
WSLG_ROOT = Path("/mnt/wslg")
WSLG_RUNTIME_DIR = WSLG_ROOT / "runtime-dir"
WSLG_PULSE_SERVER = WSLG_ROOT / "PulseServer"
X11_SOCKET_DIRS = (Path("/tmp/.X11-unix"), WSLG_ROOT / ".X11-unix")


def _env_mapping(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def graphical_env_snapshot(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the currently defined graphical environment variables."""

    source = _env_mapping(env)
    snapshot: dict[str, str] = {}
    for key in GRAPHICAL_ENV_KEYS:
        value = str(source.get(key, "")).strip()
        if value:
            snapshot[key] = value
    return snapshot


def is_wsl(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the current process appears to be running inside WSL."""

    source = _env_mapping(env)
    if source.get("WSL_DISTRO_NAME") or source.get("WSL_INTEROP"):
        return True

    if not WSL_PROC_VERSION_PATH.exists():
        return False

    try:
        return "microsoft" in WSL_PROC_VERSION_PATH.read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _wayland_socket_path(env: Mapping[str, str] | None = None) -> Path | None:
    source = _env_mapping(env)
    runtime_dir = str(source.get("XDG_RUNTIME_DIR", "")).strip()
    display_name = str(source.get("WAYLAND_DISPLAY", "")).strip()
    if not runtime_dir or not display_name:
        return None
    return Path(runtime_dir) / display_name


def _x11_socket_candidates(display: str) -> list[Path]:
    normalized = str(display).strip()
    if not normalized:
        return []
    if normalized.startswith(":"):
        display_number = normalized[1:].split(".", 1)[0] or "0"
        return [directory / f"X{display_number}" for directory in X11_SOCKET_DIRS]
    return []


def recommended_graphical_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return stable graphical env overrides that Arthexis can safely supply."""

    if not sys.platform.startswith("linux"):
        return {}

    source = _env_mapping(env)
    recommended: dict[str, str] = {}

    if is_wsl(source):
        if WSLG_RUNTIME_DIR.exists():
            recommended["XDG_RUNTIME_DIR"] = str(WSLG_RUNTIME_DIR)
            if (WSLG_RUNTIME_DIR / "wayland-0").exists():
                recommended["WAYLAND_DISPLAY"] = "wayland-0"

        if any(candidate.exists() for candidate in _x11_socket_candidates(":0")):
            recommended["DISPLAY"] = str(source.get("DISPLAY", "")).strip() or ":0"

        if WSLG_PULSE_SERVER.exists():
            recommended["PULSE_SERVER"] = f"unix:{WSLG_PULSE_SERVER}"

    return recommended


def effective_graphical_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return graphical env values after applying safe local overrides."""

    effective = graphical_env_snapshot(env)
    effective.update(recommended_graphical_env(env))
    return effective


def build_graphical_subprocess_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a subprocess environment with repaired graphical settings."""

    source = dict(_env_mapping(env))
    source.update(effective_graphical_env(source))
    return source


def has_graphical_display(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the current process can open graphical UI windows."""

    if not sys.platform.startswith("linux"):
        return True

    effective = effective_graphical_env(env)
    wayland_socket = _wayland_socket_path(effective)
    if wayland_socket is not None and wayland_socket.exists():
        return True

    display = effective.get("DISPLAY", "")
    if display and any(candidate.exists() for candidate in _x11_socket_candidates(display)):
        return True

    return bool(effective.get("DISPLAY") or effective.get("WAYLAND_DISPLAY"))
