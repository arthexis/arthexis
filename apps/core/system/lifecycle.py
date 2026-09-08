from __future__ import annotations

import os
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_ROOT = Path("/opt/arthexis")
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
        root or os.environ.get("ARTHEXIS_INSTALL_ROOT") or DEFAULT_ROOT
    ).expanduser()
    return InstallationLayout(
        root=selected_root,
        checkout=selected_root / DEFAULT_CHECKOUT_NAME,
        environment=selected_root / DEFAULT_ENVIRONMENT_NAME,
    )


def ensure_environment(current: InstallationLayout | None = None) -> Path:
    """Create the application virtual environment if it does not already exist."""
    selected = current or layout()
    if not selected.python.exists():
        selected.root.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(selected.environment)
    return selected.python


def run_python(
    arguments: Iterable[str],
    *,
    current: InstallationLayout | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the installation's Python interpreter from a deterministic directory."""
    selected = current or layout()
    python = ensure_environment(selected)
    return subprocess.run(
        [str(python), *arguments],
        cwd=Path(cwd) if cwd is not None else selected.checkout,
        check=check,
        text=True,
    )


def install_project(
    *,
    current: InstallationLayout | None = None,
    editable: bool = False,
) -> None:
    """Install the checkout into its dedicated virtual environment."""
    selected = current or layout()
    arguments = ["-m", "pip", "install"]
    if editable:
        arguments.extend(["-e", str(selected.checkout)])
    else:
        arguments.extend(["--upgrade", str(selected.checkout)])
    run_python(arguments, current=selected)


def run_manage(
    command: str,
    *arguments: str,
    current: InstallationLayout | None = None,
) -> None:
    """Run one Django management command from the installed checkout."""
    selected = current or layout()
    run_python(
        ["manage.py", command, *arguments],
        current=selected,
        cwd=selected.checkout,
    )


def migrate(*, current: InstallationLayout | None = None) -> None:
    run_manage("migrate", "--noinput", current=current)


def collectstatic(*, current: InstallationLayout | None = None) -> None:
    run_manage("collectstatic", "--noinput", current=current)


def _prepare(
    *,
    current: InstallationLayout | None = None,
    editable: bool = False,
    run_migrations: bool = True,
    run_collectstatic: bool = True,
) -> InstallationLayout:
    """Prepare dependencies and Django state after GWAY has placed the checkout."""
    selected = current or layout()
    if not selected.checkout.is_dir():
        raise FileNotFoundError(f"installation checkout does not exist: {selected.checkout}")
    ensure_environment(selected)
    install_project(current=selected, editable=editable)
    if run_migrations:
        migrate(current=selected)
    if run_collectstatic:
        collectstatic(current=selected)
    return selected


def install(
    *,
    root: str | Path | None = None,
    editable: bool = False,
) -> InstallationLayout:
    """Run Arthexis application preparation after a GWAY install."""
    return _prepare(current=layout(root), editable=editable)


def upgrade(*, root: str | Path | None = None) -> InstallationLayout:
    """Run Arthexis application preparation after GWAY updates the checkout."""
    return _prepare(current=layout(root))


def current_python() -> str:
    """Return the interpreter currently executing Arthexis."""
    return sys.executable
