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
import tempfile
from pathlib import Path


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str]) -> None:
    """Execute a subprocess command and require success.

    Args:
        command: Command and arguments to execute.
        cwd: Working directory for the subprocess, if one is required.
        env: Environment variables passed to the subprocess.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: If the command exits unsuccessfully.
    """

    subprocess.run(command, check=True, cwd=cwd, env=env)


def find_repo_root(start: Path) -> Path:
    """Locate the repository root by traversing upward to ``pyproject.toml``.

    Args:
        start: Directory from which the upward search should begin.

    Returns:
        Filesystem path to the repository root.

    Raises:
        FileNotFoundError: If no parent directory contains ``pyproject.toml``.
    """

    repo_root = start
    while not (repo_root / "pyproject.toml").is_file():
        if repo_root.parent == repo_root:
            raise FileNotFoundError(
                "Could not find repository root containing pyproject.toml."
            )
        repo_root = repo_root.parent

    return repo_root


def sanitized_environment(repo_root: Path) -> dict[str, str]:
    """Build a subprocess environment that does not rely on checkout imports.

    Args:
        repo_root: Filesystem path to the repository root.

    Returns:
        Environment variables for subprocess execution with repo-root
        ``PYTHONPATH`` entries removed.
    """

    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

    raw_pythonpath = env.get("PYTHONPATH")
    if raw_pythonpath:
        repo_root_str = str(repo_root)
        repo_root_real = os.path.realpath(repo_root_str)
        cleaned_entries = [
            entry
            for entry in raw_pythonpath.split(os.pathsep)
            if entry
            and os.path.realpath(entry) not in {repo_root_real, os.path.realpath(".")}
        ]
        if cleaned_entries:
            env["PYTHONPATH"] = os.pathsep.join(cleaned_entries)
        else:
            env.pop("PYTHONPATH", None)

    return env


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

    env = sanitized_environment(repo_root)
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
    with tempfile.TemporaryDirectory() as temp_dir:
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
            cwd=Path(temp_dir),
            env=env,
        )


def main() -> int:
    """Run the editable-install import check for the current repository.

    Returns:
        Process exit status.
    """

    editable_import_check(find_repo_root(Path(__file__).resolve().parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
