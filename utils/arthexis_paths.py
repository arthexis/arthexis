"""Central filesystem path contract for checkout and installed Arthexis modes.

This module is intentionally side-effect free: resolving paths never creates directories,
never infers installed mode from filesystem state, and does not depend on GWAY or any
other external project manager. Consumers can adopt it incrementally, whether Arthexis
is installed directly or managed as a convenience by GWAY.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ArthexisMode(StrEnum):
    """Supported filesystem operating modes."""

    CHECKOUT = "checkout"
    INSTALLED = "installed"


_PATH_ENV_VARS = {
    "app_dir": "ARTHEXIS_APP_DIR",
    "config_dir": "ARTHEXIS_CONFIG_DIR",
    "data_dir": "ARTHEXIS_DATA_DIR",
    "log_dir": "ARTHEXIS_LOG_DIR",
    "cache_dir": "ARTHEXIS_CACHE_DIR",
    "run_dir": "ARTHEXIS_RUN_DIR",
}


@dataclass(frozen=True, slots=True)
class ArthexisPaths:
    """Resolved filesystem locations for one Arthexis process."""

    mode: ArthexisMode
    app_dir: Path
    config_dir: Path
    data_dir: Path
    log_dir: Path
    cache_dir: Path
    run_dir: Path

    @property
    def writable_dirs(self) -> tuple[Path, ...]:
        """Return directories intended to contain mutable runtime/admin state."""

        return (
            self.config_dir,
            self.data_dir,
            self.log_dir,
            self.cache_dir,
            self.run_dir,
        )


def _coerce_mode(value: ArthexisMode | str | None) -> ArthexisMode:
    if value is None:
        return ArthexisMode.CHECKOUT
    if isinstance(value, ArthexisMode):
        return value

    normalized = value.strip().lower()
    try:
        return ArthexisMode(normalized)
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in ArthexisMode)
        raise ValueError(f"Unknown Arthexis mode {value!r}; expected one of: {choices}") from exc


def _default_paths(mode: ArthexisMode, project_root: Path) -> dict[str, Path]:
    if mode is ArthexisMode.INSTALLED:
        return {
            "app_dir": Path("/opt/arthexis/current"),
            "config_dir": Path("/etc/arthexis"),
            "data_dir": Path("/var/lib/arthexis"),
            "log_dir": Path("/var/log/arthexis"),
            "cache_dir": Path("/var/cache/arthexis"),
            "run_dir": Path("/run/arthexis"),
        }

    state_root = project_root / ".arthexis"
    return {
        "app_dir": project_root,
        "config_dir": state_root / "config",
        "data_dir": state_root / "data",
        "log_dir": state_root / "log",
        "cache_dir": state_root / "cache",
        "run_dir": state_root / "run",
    }


def resolve_arthexis_paths(
    *,
    project_root: str | os.PathLike[str] | None = None,
    mode: ArthexisMode | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ArthexisPaths:
    """Resolve Arthexis filesystem paths without touching the filesystem.

    ``mode`` is explicit when supplied. Otherwise ``ARTHEXIS_MODE`` is consulted and
    checkout mode is the default. The presence of ``/opt/arthexis`` is deliberately
    irrelevant, so a developer checkout never becomes an installed instance by accident.

    Every logical path can be overridden independently with its ``ARTHEXIS_*_DIR``
    environment variable. This makes writable locations redirectable in CI and tests
    without requiring root privileges. Selection is intentionally Arthexis-owned and
    requires no GWAY-specific environment, registry, or installation state.
    """

    env = os.environ if environ is None else environ
    resolved_mode = _coerce_mode(mode if mode is not None else env.get("ARTHEXIS_MODE"))
    root = Path(project_root) if project_root is not None else Path.cwd()
    defaults = _default_paths(resolved_mode, root)

    resolved: dict[str, Path] = {}
    for field_name, env_name in _PATH_ENV_VARS.items():
        override = env.get(env_name)
        resolved[field_name] = Path(override) if override else defaults[field_name]

    return ArthexisPaths(mode=resolved_mode, **resolved)
