"""Helpers for resolving the validated project Python interpreter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.helpers.venv_path import resolve_venv_dir


def _env_flag_enabled(value: str | None) -> bool:
    """Return whether an environment flag uses the shared truthy contract."""

    return value in {"1", "true", "TRUE", "yes", "YES"}


def _qa_cache_key_preferred() -> bool:
    """Return whether QA requirements should be preferred for cache resolution."""

    return _env_flag_enabled(os.environ.get("ARTHEXIS_INCLUDE_QA_REQUIREMENTS")) or (
        _env_flag_enabled(os.environ.get("ARTHEXIS_INSTALL_PREVIEW_DEPS"))
    )


def _hardware_cache_key_active(base_dir: Path) -> bool:
    """Return whether hardware requirements should affect cache resolution."""

    if _env_flag_enabled(
        os.environ.get("ARTHEXIS_INSTALL_HARDWARE_DEPS")
    ) or _env_flag_enabled(os.environ.get("ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS")):
        return True

    lock_dir = base_dir / ".locks"
    if (lock_dir / "control.lck").is_file():
        return True

    role_lock = lock_dir / "role.lck"
    if (
        role_lock.is_file()
        and role_lock.read_text(encoding="utf-8").strip() == "Control"
    ):
        return True

    lcd_lock = os.environ.get("ARTHEXIS_LCD_LOCK", "lcd_screen.lck")
    rfid_service_lock = os.environ.get("ARTHEXIS_RFID_SERVICE_LOCK", "rfid-service.lck")
    rfid_lock = os.environ.get("ARTHEXIS_RFID_LOCK", "rfid.lck")
    return any(
        (lock_dir / lock_name).is_file()
        for lock_name in (lcd_lock, rfid_service_lock, rfid_lock)
    )


def _venv_python_candidates(venv_dir: Path) -> tuple[Path, ...]:
    """Return interpreter candidates under ``venv_dir`` in platform order."""

    candidates = (
        venv_dir / "Scripts" / "python.exe",
        venv_dir / "bin" / "python",
    )
    if sys.platform != "win32":
        candidates = tuple(reversed(candidates))
    return candidates


def project_python_candidates(base_dir: Path) -> tuple[Path, ...]:
    """Return candidate interpreter paths for the repository virtual environment.

    Args:
        base_dir: Repository root that may contain the project virtual environment.

    Returns:
        Candidate interpreter paths ordered by platform preference.
    """

    resolved_base_dir = base_dir.resolve()
    candidate_venv_dirs: list[Path] = []

    if explicit_venv := os.environ.get("ARTHEXIS_VENV_DIR"):
        candidate_venv_dirs.append(Path(explicit_venv).expanduser().resolve())
    elif os.environ.get("ARTHEXIS_ENV_ROOT"):
        include_hardware = _hardware_cache_key_active(resolved_base_dir)
        include_ci_order = (True, False) if _qa_cache_key_preferred() else (False, True)
        candidate_venv_dirs.extend(
            resolve_venv_dir(resolved_base_dir, include_ci, include_hardware)
            for include_ci in include_ci_order
        )

    candidate_venv_dirs.extend(
        (resolved_base_dir / ".venv", resolved_base_dir / "venv")
    )
    candidates: list[Path] = []
    for venv_dir in candidate_venv_dirs:
        candidates.extend(_venv_python_candidates(venv_dir))
    return tuple(candidates)


def _is_runnable_project_python(candidate: Path) -> bool:
    """Return whether ``candidate`` can start a Python process successfully.

    Args:
        candidate: Candidate interpreter path inside the repository virtualenv.

    Returns:
        ``True`` when the candidate exists and can launch a trivial Python command;
        otherwise ``False``.

    Raises:
        No exceptions are raised. Launch failures are treated as non-runnable
        candidates so the caller can fall back to another interpreter.
    """

    if not candidate.is_file():
        return False

    try:
        result = subprocess.run(
            [str(candidate), "-c", "raise SystemExit(0)"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False

    return result.returncode == 0


def resolve_project_python(base_dir: Path, *, fallback_to_current: bool = True) -> str:
    """Return the preferred interpreter for repository-managed commands.

    Args:
        base_dir: Repository root that may contain the project virtual environment.
        fallback_to_current: Return ``sys.executable`` when no managed candidate
            is runnable. Disable this when callers must enforce a project venv.

    Returns:
        The project virtual environment interpreter when present and runnable;
        otherwise the currently running Python interpreter.

    Raises:
        FileNotFoundError: When no managed candidate is runnable and fallback is
            disabled.
    """

    for candidate in project_python_candidates(base_dir):
        if _is_runnable_project_python(candidate):
            return str(candidate)
    if not fallback_to_current:
        raise FileNotFoundError("Project virtual environment Python not found.")
    return sys.executable
