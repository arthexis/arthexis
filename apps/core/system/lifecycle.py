from __future__ import annotations

import os
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_MANAGED_ROOT = Path("/opt/arthexis")
DEFAULT_CHECKOUT_NAME = "app"
DEFAULT_ENVIRONMENT_NAME = ".venv"


@dataclass(frozen=True)
class ManagedLayout:
    root: Path
    checkout: Path
    environment: Path

    @property
    def python(self) -> Path:
        if os.name == "nt":
            return self.environment / "Scripts" / "python.exe"
        return self.environment / "bin" / "python"


def managed_layout(root: str | Path | None = None) -> ManagedLayout:
    """Return the canonical filesystem layout for a GWAY-managed install."""
    selected_root = Path(
        root or os.environ.get("ARTHEXIS_MANAGED_ROOT") or DEFAULT_MANAGED_ROOT
    ).expanduser()
    return ManagedLayout(
        root=selected_root,
        checkout=selected_root / DEFAULT_CHECKOUT_NAME,
        environment=selected_root / DEFAULT_ENVIRONMENT_NAME,
    )


def ensure_environment(layout: ManagedLayout | None = None) -> Path:
    """Create the managed virtual environment if it does not already exist."""
    current = layout or managed_layout()
    if not current.python.exists():
        current.root.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(current.environment)
    return current.python


def run_python(
    arguments: Iterable[str],
    *,
    layout: ManagedLayout | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the managed Python interpreter with a deterministic working directory."""
    current = layout or managed_layout()
    python = ensure_environment(current)
    return subprocess.run(
        [str(python), *arguments],
        cwd=Path(cwd) if cwd is not None else current.checkout,
        check=check,
        text=True,
    )


def install_project(
    *,
    layout: ManagedLayout | None = None,
    editable: bool = False,
) -> None:
    """Install the managed checkout into its dedicated virtual environment."""
    current = layout or managed_layout()
    target = str(current.checkout)
    if editable:
        target = f"-e {target}"
        run_python(["-m", "pip", "install", "-e", str(current.checkout)], layout=current)
        return
    run_python(["-m", "pip", "install", "--upgrade", str(current.checkout)], layout=current)


def run_manage(
    command: str,
    *arguments: str,
    layout: ManagedLayout | None = None,
) -> None:
    """Run one Django management command from the managed checkout."""
    current = layout or managed_layout()
    run_python(
        ["manage.py", command, *arguments],
        layout=current,
        cwd=current.checkout,
    )


def migrate(*, layout: ManagedLayout | None = None) -> None:
    run_manage("migrate", "--noinput", layout=layout)


def collectstatic(*, layout: ManagedLayout | None = None) -> None:
    run_manage("collectstatic", "--noinput", layout=layout)


def prepare_managed_install(
    *,
    layout: ManagedLayout | None = None,
    editable: bool = False,
    run_migrations: bool = True,
    run_collectstatic: bool = True,
) -> ManagedLayout:
    """Prepare dependencies and Django state for a managed checkout.

    Git checkout/update and service restart deliberately remain GWAY-owned.
    """
    current = layout or managed_layout()
    if not current.checkout.is_dir():
        raise FileNotFoundError(f"managed checkout does not exist: {current.checkout}")
    ensure_environment(current)
    install_project(layout=current, editable=editable)
    if run_migrations:
        migrate(layout=current)
    if run_collectstatic:
        collectstatic(layout=current)
    return current


def current_python() -> str:
    """Return the interpreter currently executing Arthexis."""
    return sys.executable
