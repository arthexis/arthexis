from __future__ import annotations

import os
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_INSTALL_ROOT = Path("/opt/arthexis")
DEFAULT_CHECKOUT_NAME = "app"
DEFAULT_ENVIRONMENT_NAME = ".venv"

# Compatibility name retained while callers introduced by #120 migrate.
DEFAULT_MANAGED_ROOT = DEFAULT_INSTALL_ROOT


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


# Compatibility type alias retained while callers introduced by #120 migrate.
ManagedLayout = InstallationLayout


def layout(root: str | Path | None = None) -> InstallationLayout:
    """Return the canonical filesystem layout for an Arthexis installation."""
    selected_root = Path(
        root or os.environ.get("ARTHEXIS_MANAGED_ROOT") or DEFAULT_INSTALL_ROOT
    ).expanduser()
    return InstallationLayout(
        root=selected_root,
        checkout=selected_root / DEFAULT_CHECKOUT_NAME,
        environment=selected_root / DEFAULT_ENVIRONMENT_NAME,
    )


def managed_layout(root: str | Path | None = None) -> InstallationLayout:
    """Compatibility alias for :func:`layout`."""
    return layout(root)


def _resolve_layout(selected: InstallationLayout | None) -> InstallationLayout:
    return selected or layout()


def ensure_environment(layout: InstallationLayout | None = None) -> Path:
    """Create the application virtual environment if it does not already exist."""
    current = _resolve_layout(layout)
    if not current.python.exists():
        current.root.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(current.environment)
    return current.python


def run_python(
    arguments: Iterable[str],
    *,
    layout: InstallationLayout | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the installation Python interpreter with a deterministic working directory."""
    current = _resolve_layout(layout)
    python = ensure_environment(current)
    return subprocess.run(
        [str(python), *arguments],
        cwd=Path(cwd) if cwd is not None else current.checkout,
        check=check,
        text=True,
    )


def install_project(
    *,
    layout: InstallationLayout | None = None,
    editable: bool = False,
) -> None:
    """Install the checkout into its dedicated virtual environment."""
    current = _resolve_layout(layout)
    arguments = ["-m", "pip", "install"]
    if editable:
        arguments.extend(["-e", str(current.checkout)])
    else:
        arguments.extend(["--upgrade", str(current.checkout)])
    run_python(arguments, layout=current)


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
    editable: bool = False,
    run_migrations: bool = True,
    run_collectstatic: bool = True,
) -> InstallationLayout:
    """Prepare dependencies and Django state for an installation checkout.

    Git checkout/update and service restart deliberately remain GWAY-owned.
    """
    current = _resolve_layout(layout)
    if not current.checkout.is_dir():
        raise FileNotFoundError(f"installation checkout does not exist: {current.checkout}")
    ensure_environment(current)
    install_project(layout=current, editable=editable)
    if run_migrations:
        migrate(layout=current)
    if run_collectstatic:
        collectstatic(layout=current)
    return current


def prepare_managed_install(
    *,
    layout: InstallationLayout | None = None,
    editable: bool = False,
    run_migrations: bool = True,
    run_collectstatic: bool = True,
) -> InstallationLayout:
    """Compatibility alias for :func:`prepare`."""
    return prepare(
        layout=layout,
        editable=editable,
        run_migrations=run_migrations,
        run_collectstatic=run_collectstatic,
    )


def current_python() -> str:
    """Return the interpreter currently executing Arthexis."""
    return sys.executable
