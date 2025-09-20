"""Process management utilities for the MCP sigil resolver server."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Dict, Any

from django.conf import settings

__all__ = [
    "get_status",
    "start_server",
    "stop_server",
    "ServerError",
    "ServerAlreadyRunningError",
    "ServerNotRunningError",
    "ServerStartError",
    "ServerStopError",
]


class ServerError(RuntimeError):
    """Base exception for MCP server process errors."""


class ServerAlreadyRunningError(ServerError):
    """Raised when attempting to start an already running server."""


class ServerNotRunningError(ServerError):
    """Raised when attempting to stop a server that is not running."""


class ServerStartError(ServerError):
    """Raised when the server cannot be started."""


class ServerStopError(ServerError):
    """Raised when the server cannot be stopped."""


_BASE_DIR = Path(settings.BASE_DIR)
_LOCK_DIR = _BASE_DIR / "locks"
_PID_FILE = _LOCK_DIR / "mcp_sigil_server.pid"
_LOG_DIR = _BASE_DIR / "logs"
_LOG_FILE = _LOG_DIR / "mcp_sigil_server.log"


def _read_pid() -> int | None:
    try:
        return int(_PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def _pid_matches_server(pid: int | None) -> bool:
    """Return True when ``pid`` represents the active MCP server."""

    if pid is None or not _pid_running(pid):
        return False

    if sys.platform.startswith("linux"):
        cmdline_path = Path("/proc") / str(pid) / "cmdline"
        try:
            raw_cmdline = cmdline_path.read_bytes()
        except FileNotFoundError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

        cmdline = raw_cmdline.decode("utf-8", errors="ignore")
        parts = [part for part in cmdline.split("\0") if part]
        expected_manage = str(_BASE_DIR / "manage.py")
        if "mcp_sigil_server" not in parts or expected_manage not in parts:
            return False

    return True


def _tail_log(limit: int = 20) -> str:
    if not _LOG_FILE.exists():
        return ""
    try:
        with _LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, maxlen=limit)
    except OSError:
        return ""
    return "".join(lines)


def get_status() -> Dict[str, Any]:
    """Return the current MCP server status and recent log output."""

    pid = _read_pid()
    running = _pid_matches_server(pid)
    last_error = ""
    if pid is not None and not running:
        last_error = "Cleared stale MCP server PID file."
        try:
            _PID_FILE.unlink()
        except FileNotFoundError:
            pass
        pid = None
    return {
        "running": running,
        "pid": pid,
        "log_excerpt": _tail_log(),
        "last_error": last_error,
    }


def start_server() -> int:
    """Start the MCP server in a detached subprocess."""

    status = get_status()
    if status["running"]:
        raise ServerAlreadyRunningError("MCP server is already running.")

    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    command = [sys.executable, str(_BASE_DIR / "manage.py"), "mcp_sigil_server"]
    env = os.environ.copy()

    try:
        with _LOG_FILE.open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(_BASE_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
    except OSError as exc:  # pragma: no cover - defensive
        raise ServerStartError(f"Unable to start MCP server: {exc}") from exc

    _PID_FILE.write_text(str(process.pid))
    return process.pid


def stop_server() -> int:
    """Stop the MCP server process."""

    pid = _read_pid()
    if pid is None or not _pid_matches_server(pid):
        try:
            _PID_FILE.unlink()
        except FileNotFoundError:
            pass
        raise ServerNotRunningError("MCP server is not running.")

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:  # pragma: no cover - defensive
        raise ServerStopError(f"Unable to stop MCP server: {exc}") from exc

    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass
    return pid
