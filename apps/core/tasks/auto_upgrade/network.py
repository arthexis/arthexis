from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from apps.core.auto_upgrade import append_auto_upgrade_log

from .locks import _read_network_failure_count, _write_network_failure_count


logger = logging.getLogger(__name__)

AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD = 3

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


def _record_network_failure(base_dir: Path, detail: str) -> int:
    count = _read_network_failure_count(base_dir) + 1
    _write_network_failure_count(base_dir, count)
    append_auto_upgrade_log(
        base_dir,
        f"Auto-upgrade network failure {count}: {detail}",
    )
    return count


def _charge_point_active(base_dir: Path) -> bool:
    lock_path = base_dir / ".locks" / "charging.lck"
    if lock_path.exists():
        return True
    try:
        from apps.ocpp import store  # type: ignore
    except Exception:
        return False
    try:
        connections = getattr(store, "connections", {})
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(connections)


def _trigger_auto_upgrade_reboot(base_dir: Path) -> None:
    try:
        subprocess.run(["sudo", "systemctl", "reboot"], check=False)
    except Exception:  # pragma: no cover - best effort reboot command
        logger.exception(
            "Failed to trigger reboot after repeated auto-upgrade network failures"
        )


def _reboot_if_no_charge_point(base_dir: Path) -> None:
    if _charge_point_active(base_dir):
        append_auto_upgrade_log(
            base_dir,
            "Skipping reboot after repeated auto-upgrade network failures; a charge point is active",
        )
        return
    append_auto_upgrade_log(
        base_dir,
        "Rebooting due to repeated auto-upgrade network failures",
    )
    _trigger_auto_upgrade_reboot(base_dir)


def _handle_network_failure_if_applicable(
    base_dir: Path, exc: subprocess.CalledProcessError
) -> bool:
    if not _is_network_failure(exc):
        return False
    detail = _extract_error_output(exc)
    failure_count = _record_network_failure(base_dir, detail)
    if failure_count >= AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD:
        _reboot_if_no_charge_point(base_dir)
    return True
