from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_INSTALL_ROOT = Path("/opt/arthexis")
DEFAULT_CHECKOUT_NAME = "app"
DEFAULT_ENVIRONMENT_NAME = ".venv"


@dataclass(frozen=True)
class InstallationLayout:
    root: Path
    checkout: Path
    environment: Path

    @property
    def python(self) -> Path:
        if os.name == "nt":
            return self.environment / "Scripts" / "python.exe"
        return self.environment / "bin" / "python"


def layout(root: str | Path | None = None) -> InstallationLayout:
    """Return the canonical filesystem layout for an Arthexis installation."""
    selected_root = Path(
        root
        or os.environ.get("ARTHEXIS_INSTALL_ROOT")
        or os.environ.get("ARTHEXIS_MANAGED_ROOT")
        or DEFAULT_INSTALL_ROOT
    ).expanduser()
    return InstallationLayout(
        root=selected_root,
        checkout=selected_root / DEFAULT_CHECKOUT_NAME,
        environment=selected_root / DEFAULT_ENVIRONMENT_NAME,
    )


def _resolve_layout(selected: InstallationLayout | None) -> InstallationLayout:
    return selected or layout()


def run_python(
    arguments: Iterable[str],
    *,
    layout: InstallationLayout | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the managed interpreter with a deterministic working directory."""
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
    """Prepare Django state for a GWAY-managed installation checkout.

    Checkout/update, environment preparation, package installation, and service
    restart deliberately remain GWAY-owned.
    """
    current = _resolve_layout(layout)
    if not current.checkout.is_dir():
        raise FileNotFoundError(f"installation checkout does not exist: {current.checkout}")
    if run_migrations:
        migrate(layout=current)
    if run_collectstatic:
        collectstatic(layout=current)
    return current


def install(*, layout: InstallationLayout | None = None) -> InstallationLayout:
    """Application preparation hook for a GWAY installation."""
    return prepare(layout=layout)


def upgrade(*, layout: InstallationLayout | None = None) -> InstallationLayout:
    """Application preparation hook for a GWAY upgrade."""
    return prepare(layout=layout)


def current_python() -> str:
    """Return the interpreter currently executing Arthexis."""
    return sys.executable
