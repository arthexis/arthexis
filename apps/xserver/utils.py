from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import socket
import subprocess


@dataclass(frozen=True)
class XServerDetection:
    """Detected local X display server details."""

    display_name: str
    host: str
    runtime_scope: str
    server_type: str
    process_name: str
    raw_data: dict[str, str]


def _run_command(*args: str) -> str:
    """Run a command and return stdout text, or an empty string on failures."""

    executable = shutil.which(args[0])
    if not executable:
        return ""
    try:
        completed = subprocess.run(
            [executable, *args[1:]],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def detect_x_server() -> XServerDetection | None:
    """Detect local X server availability and metadata from environment/system state."""

    display_name = os.environ.get("DISPLAY", "").strip()
    if not display_name:
        return None

    runtime_scope = "local"
    host = socket.gethostname()
    if display_name.startswith(":"):
        runtime_scope = "local"
    elif display_name.lower().startswith("unix:"):
        runtime_scope = "local"
        host = "unix"
    else:
        runtime_scope = "remote"
        host = display_name.split(":", 1)[0]

    server_type = "x11"
    xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if xdg_session_type == "wayland":
        server_type = "xwayland"

    process_name = ""
    process_listing = _run_command("ps", "-eo", "comm")
    for candidate in ("Xwayland", "Xorg", "Xephyr", "Xvfb"):
        if candidate in process_listing:
            process_name = candidate
            server_type = {
                "Xwayland": "xwayland",
                "Xorg": "xorg",
                "Xephyr": "xephyr",
                "Xvfb": "xvfb",
            }[candidate]
            break

    unix_socket_available = False
    display_number = display_name.split(":")[-1].split(".")[0]
    if display_number.isdigit():
        unix_socket_available = Path(f"/tmp/.X11-unix/X{display_number}").exists()

    if not process_name and not unix_socket_available:
        return None

    return XServerDetection(
        display_name=display_name,
        host=host,
        runtime_scope=runtime_scope,
        server_type=server_type,
        process_name=process_name,
        raw_data={
            "display": display_name,
            "xdg_session_type": xdg_session_type,
            "unix_socket_available": str(unix_socket_available).lower(),
        },
    )


def has_x_server() -> bool:
    """Return whether an X display server is currently detected."""

    return detect_x_server() is not None
