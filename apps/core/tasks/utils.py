from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import psutil

from apps.core.auto_upgrade import auto_upgrade_base_dir
from utils.revision import get_revision


logger = logging.getLogger(__name__)

_NETWORK_FAILURE_PATTERNS = (
    "could not resolve host",
    "couldn't resolve host",
    "failed to connect",
    "couldn't connect to server",
    "connection reset by peer",
    "recv failure",
    "connection timed out",
    "network is unreachable",
    "temporary failure in name resolution",
    "tls connection was non-properly terminated",
    "gnutls recv error",
    "name or service not known",
    "could not resolve proxy",
    "no route to host",
)


def _project_base_dir() -> Path:
    """Return the filesystem base directory for runtime operations."""

    return auto_upgrade_base_dir()


def _read_process_cmdline(pid: int) -> list[str]:
    """Return the command line for a process when available."""

    try:
        return psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _read_process_start_time(pid: int) -> float | None:
    """Return the process start time in epoch seconds when available."""

    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _extract_error_output(exc: subprocess.CalledProcessError) -> str:
    parts: list[str] = []
    for attr in ("stderr", "stdout", "output"):
        value = getattr(exc, attr, None)
        if not value:
            continue
        if isinstance(value, bytes):
            try:
                value = value.decode()
            except Exception:  # pragma: no cover - best effort decoding
                value = value.decode(errors="ignore")
        parts.append(str(value))
    detail = " ".join(part.strip() for part in parts if part)
    if not detail:
        detail = str(exc)
    return detail


def _is_network_failure(exc: subprocess.CalledProcessError) -> bool:
    command = exc.cmd
    if isinstance(command, (list, tuple)):
        if not command:
            return False
        first = str(command[0])
    else:
        command_str = str(command)
        first = command_str.split()[0] if command_str else ""
    if "git" not in first:
        return False
    detail = _extract_error_output(exc).lower()
    return any(pattern in detail for pattern in _NETWORK_FAILURE_PATTERNS)


def _current_revision(base_dir: Path) -> str:
    """Return the current git revision when available."""

    del base_dir  # Base directory handled by shared revision helper.

    try:
        return get_revision()
    except Exception:  # pragma: no cover - defensive fallback
        logger.warning(
            "Failed to resolve git revision for auto-upgrade logging", exc_info=True
        )
        return ""
