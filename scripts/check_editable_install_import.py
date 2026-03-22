#!/usr/bin/env python3
"""Validate editable-install import behavior for the local Arthexis checkout.

The check re-installs the current repository in editable mode with build
isolation and dependency resolution disabled, then runs a plain Python command
that imports ``arthexis``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run(command: list[str], *, env: dict[str, str]) -> None:
    """Execute a subprocess command and require success.

    Args:
        command: Command and arguments to execute.
        env: Environment variables passed to the subprocess.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: If the command exits unsuccessfully.
    """

    subprocess.run(command, check=True, env=env)


def editable_import_check(repo_root: Path) -> None:
    """Install the repository editably and verify ``import arthexis`` succeeds.

    Args:
        repo_root: Filesystem path to the repository root.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: If installation or the import script
            fails.
    """

    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "--no-deps",
            "-e",
            str(repo_root),
        ],
        env=env,
    )
    run(
        [
            sys.executable,
            "-c",
            (
                "import arthexis; "
                "print(arthexis.__file__); "
                "print(getattr(arthexis, '__version__', 'missing'))"
            ),
        ],
        env=env,
    )


def main() -> int:
    """Run the editable-install import check for the current repository.

    Returns:
        Process exit status.
    """

    editable_import_check(Path(__file__).resolve().parents[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
