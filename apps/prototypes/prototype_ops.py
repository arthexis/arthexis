"""Compatibility helpers for retired prototype runtime records."""

from __future__ import annotations

import os
import re
import subprocess
from collections import OrderedDict
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.prototypes.models import Prototype

RETIREMENT_MESSAGE = (
    "Prototype runtime scaffolding has been retired. Records are metadata only."
)
ACTIVE_PROTOTYPE_LOCK = "active_prototype.lck"
PREVIOUS_BACKEND_PORT_LOCK = "prototype_previous_backend_port.lck"
PROTOTYPE_ENV_START = "# BEGIN ARTHEXIS PROTOTYPE"
PROTOTYPE_ENV_END = "# END ARTHEXIS PROTOTYPE"
_PROTOTYPE_BLOCK_RE = re.compile(
    rf"\n?{re.escape(PROTOTYPE_ENV_START)}\n.*?{re.escape(PROTOTYPE_ENV_END)}\n?",
    re.DOTALL,
)


def _base_dir(base_dir: Path | None = None) -> Path:
    """Return the project base directory used for legacy prototype state.

    Parameters:
        base_dir: Optional explicit base directory override.

    Returns:
        Path: The resolved project base directory.
    """

    return Path(base_dir or settings.BASE_DIR)


def active_prototype_lock_path(base_dir: Path | None = None) -> Path:
    """Return the lock file that tracked the active prototype slug.

    Parameters:
        base_dir: Optional explicit base directory override.

    Returns:
        Path: The active prototype lock path.
    """

    return _base_dir(base_dir) / ".locks" / ACTIVE_PROTOTYPE_LOCK


def backend_port_lock_path(base_dir: Path | None = None) -> Path:
    """Return the backend port lock path used by legacy prototype activation.

    Parameters:
        base_dir: Optional explicit base directory override.

    Returns:
        Path: The backend port lock path.
    """

    return _base_dir(base_dir) / ".locks" / "backend_port.lck"


def previous_backend_port_lock_path(base_dir: Path | None = None) -> Path:
    """Return the saved pre-prototype backend port lock path.

    Parameters:
        base_dir: Optional explicit base directory override.

    Returns:
        Path: The previous backend port lock path.
    """

    return _base_dir(base_dir) / ".locks" / PREVIOUS_BACKEND_PORT_LOCK


def env_path(base_dir: Path | None = None) -> Path:
    """Return the environment file that stored legacy prototype overrides.

    Parameters:
        base_dir: Optional explicit base directory override.

    Returns:
        Path: The managed environment file path.
    """

    return _base_dir(base_dir) / "arthexis.env"


def _rewrite_managed_env_block(path: Path, values: OrderedDict[str, str]) -> None:
    """Rewrite the managed prototype block in ``path`` with ``values``.

    Parameters:
        path: Environment file to update.
        values: Managed environment values to persist.

    Returns:
        None.
    """

    current = path.read_text(encoding="utf-8") if path.exists() else ""
    stripped = _PROTOTYPE_BLOCK_RE.sub("\n", current).strip()
    if not values:
        if stripped:
            path.write_text(stripped + "\n", encoding="utf-8")
        elif path.exists():
            path.unlink()
        return

    block_lines = [PROTOTYPE_ENV_START]
    block_lines.extend(f"{key}={value}" for key, value in values.items())
    block_lines.append(PROTOTYPE_ENV_END)
    next_text = f"{stripped}\n\n{'\n'.join(block_lines)}".strip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(next_text, encoding="utf-8")


def retire_prototype(prototype: Prototype, *, note: str = "") -> Prototype:
    """Mark a prototype record as retired metadata.

    Parameters:
        prototype: The prototype record to update.
        note: Optional administrative note recorded on the row.

    Returns:
        Prototype: The updated prototype record.
    """

    prototype.is_active = False
    prototype.is_runnable = False
    if prototype.retired_at is None:
        prototype.retired_at = timezone.now()
    if note:
        prototype.retirement_notes = note
    prototype.save(update_fields=["is_active", "is_runnable", "retired_at", "retirement_notes"])
    return prototype


def clear_legacy_runtime_state(*, base_dir: Path | None = None) -> None:
    """Clear legacy prototype env and lock files without re-enabling runtime support.

    Parameters:
        base_dir: Optional explicit base directory override.

    Returns:
        None.
    """

    resolved_base_dir = _base_dir(base_dir)
    _rewrite_managed_env_block(env_path(resolved_base_dir), OrderedDict())

    lock_path = active_prototype_lock_path(resolved_base_dir)
    if lock_path.exists():
        lock_path.unlink()

    port_lock = backend_port_lock_path(resolved_base_dir)
    previous_port_lock = previous_backend_port_lock_path(resolved_base_dir)
    previous_port = (
        previous_port_lock.read_text(encoding="utf-8").strip()
        if previous_port_lock.exists()
        else ""
    )
    if previous_port:
        port_lock.parent.mkdir(parents=True, exist_ok=True)
        port_lock.write_text(previous_port + "\n", encoding="utf-8")
    elif port_lock.exists():
        port_lock.unlink()
    if previous_port_lock.exists():
        previous_port_lock.unlink()

    Prototype.objects.filter(is_active=True).update(is_active=False)


def restart_suite(*, base_dir: Path | None = None, force_stop: bool = False) -> None:
    """Restart the suite through the existing lifecycle scripts.

    Parameters:
        base_dir: Optional explicit base directory override.
        force_stop: Whether to pass ``--force`` to ``stop.sh``.

    Returns:
        None.

    Raised exceptions:
        CalledProcessError: Raised when a lifecycle script exits unsuccessfully.
    """

    resolved_base_dir = _base_dir(base_dir)
    stop_command = ["./stop.sh"]
    if force_stop:
        stop_command.append("--force")
    subprocess.run(stop_command, cwd=resolved_base_dir, check=True, env=os.environ.copy())
    subprocess.run(
        ["./start.sh", "--await"],
        cwd=resolved_base_dir,
        check=True,
        env=os.environ.copy(),
    )
