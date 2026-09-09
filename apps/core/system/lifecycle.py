from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config.roles import SUPPORTED_ROLES, normalize_role

DEFAULT_INSTALL_ROOT = Path("/opt/arthexis")
DEFAULT_CHECKOUT_NAME = "app"


@dataclass(frozen=True)
class InstallationLayout:
    root: Path
    checkout: Path


def layout(root: str | Path | None = None) -> InstallationLayout:
    """Return the application-visible layout for a GWAY-managed installation."""
    selected_root = Path(
        root
        or os.environ.get("ARTHEXIS_INSTALL_ROOT")
        or os.environ.get("ARTHEXIS_MANAGED_ROOT")
        or DEFAULT_INSTALL_ROOT
    ).expanduser()
    return InstallationLayout(
        root=selected_root,
        checkout=selected_root / DEFAULT_CHECKOUT_NAME,
    )


def _resolve_layout(selected: InstallationLayout | None) -> InstallationLayout:
    return selected or layout()


def _parse_lifecycle_arguments(arguments: tuple[str, ...]) -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--role")
    namespace, unknown = parser.parse_known_args(arguments)
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")
    if namespace.role is None:
        return None
    role = normalize_role(namespace.role)
    if role not in SUPPORTED_ROLES:
        parser.error(
            f"invalid --role {namespace.role!r}; choose from {', '.join(SUPPORTED_ROLES)}"
        )
    return role


def _role_lock(current: InstallationLayout) -> Path:
    return current.checkout / ".locks" / "role.lck"


def _persist_role(role: str, current: InstallationLayout) -> None:
    role_lock = _role_lock(current)
    role_lock.parent.mkdir(parents=True, exist_ok=True)
    role_lock.write_text(f"{role}\n", encoding="utf-8")


def run_python(
    arguments: Iterable[str],
    *,
    layout: InstallationLayout | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the current interpreter with a deterministic working directory."""
    current = _resolve_layout(layout)
    return subprocess.run(
        [current_python(), *arguments],
        cwd=Path(cwd) if cwd is not None else current.checkout,
        check=check,
        text=True,
    )


def run_manage(
    command: str,
    *arguments: str,
    layout: InstallationLayout | None = None,
) -> None:
    """Run one Django management command from the installation checkout."""
    current = _resolve_layout(layout)
    run_python(
        ["manage.py", command, *arguments],
        layout=current,
        cwd=current.checkout,
    )


def migrate(*, layout: InstallationLayout | None = None) -> None:
    run_manage("migrate", "--noinput", layout=layout)


def collectstatic(*, layout: InstallationLayout | None = None) -> None:
    run_manage("collectstatic", "--noinput", layout=layout)


def prepare(
    *,
    layout: InstallationLayout | None = None,
    run_migrations: bool = True,
    run_collectstatic: bool = True,
) -> InstallationLayout:
    """Prepare application state for a GWAY-managed installation checkout.

    GWAY owns checkout/update, environment preparation, package installation,
    lifecycle invocation, and service mechanics. Arthexis owns application
    preparation performed by this hook.
    """
    current = _resolve_layout(layout)
    if not current.checkout.is_dir():
        raise FileNotFoundError(f"installation checkout does not exist: {current.checkout}")
    if run_migrations:
        migrate(layout=current)
    if run_collectstatic:
        collectstatic(layout=current)
    return current


def _prepare_for_role(
    arguments: tuple[str, ...],
    *,
    layout: InstallationLayout | None = None,
) -> InstallationLayout:
    current = _resolve_layout(layout)
    role = _parse_lifecycle_arguments(arguments)
    if role is not None:
        _persist_role(role, current)
    return prepare(layout=current)


def install(*arguments: str, layout: InstallationLayout | None = None) -> InstallationLayout:
    """Application preparation hook for a GWAY installation."""
    return _prepare_for_role(arguments, layout=layout)


def upgrade(*arguments: str, layout: InstallationLayout | None = None) -> InstallationLayout:
    """Application preparation hook for a GWAY upgrade."""
    return _prepare_for_role(arguments, layout=layout)


def current_python() -> str:
    """Return the interpreter currently executing Arthexis."""
    return sys.executable
