"""Regression tests for Python interpreter detection in shell helpers."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.utils import bash_path



def _write_executable(path: Path, content: str) -> None:
    """Write an executable helper script to *path* with *content*."""

    path.write_text(content)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR)


def _find_bash() -> str:
    """Return a runnable bash executable path for shell-helper tests."""

    if os.access("/bin/bash", os.X_OK):
        return "/bin/bash"

    if os.access("/usr/bin/bash", os.X_OK):
        return "/usr/bin/bash"

    bash_from_path = _find_bash_on_path()
    if bash_from_path:
        return bash_from_path

    pytest.skip("bash is required for scripts/helpers/common.sh tests")


def _is_windows_bash_launcher_shim(path: str) -> bool:
    """Return ``True`` when ``path`` points to known Windows bash launcher shims."""

    if os.name != "nt":
        return False

    normalized = path.replace("\\", "/").lower()
    if normalized.endswith("/windows/system32/bash.exe"):
        return True
    return "/microsoft/windowsapps/" in normalized


def _find_bash_on_path() -> str | None:
    """Return the first non-shim ``bash`` executable found on ``PATH``."""

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    seen: set[str] = set()
    for entry in path_entries:
        lookup_path = entry or os.curdir
        bash_from_entry = shutil.which("bash", path=lookup_path)
        if bash_from_entry is None:
            continue

        normalized = bash_from_entry.lower() if os.name == "nt" else bash_from_entry
        if normalized in seen:
            continue
        seen.add(normalized)

        if not _is_windows_bash_launcher_shim(bash_from_entry):
            return bash_from_entry

    return None


def _isolated_path(fake_bin: Path) -> str:
    """Return a PATH value restricted to the temporary fake binary directory."""

    return bash_path(fake_bin)


def _shell_test_env(path_value: str) -> dict[str, str]:
    """Return a minimal environment for subprocess shell helper tests."""

    env: dict[str, str] = {}
    home = os.environ.get("HOME")
    if home is not None:
        env["HOME"] = home

    env["PATH"] = path_value
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT")
        if system_root is not None:
            env["SYSTEMROOT"] = system_root
        env["Path"] = path_value

    return env


def _find_sort() -> str:
    """Return a runnable sort executable path for shell-helper tests."""

    sort_path = shutil.which("sort")
    if sort_path:
        return bash_path(Path(sort_path))

    pytest.skip("'sort' is required for scripts/helpers/common.sh tests")


def _run_arthexis_python_bin(
    *,
    env: dict[str, str],
    cwd: Path,
    script_source: str = "source scripts/helpers/common.sh\n",
) -> subprocess.CompletedProcess[str]:
    """Run ``arthexis_python_bin`` and return the completed process."""

    script = script_source + "arthexis_python_bin\n"
    bash_executable = _find_bash()
    return subprocess.run(
        [bash_executable, "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _install_sort_shim(fake_bin: Path) -> None:
    """Install a fake ``sort`` command in ``fake_bin`` that forwards to system sort."""

    sort_path = shlex.quote(_find_sort())
    _write_executable(fake_bin / "sort", f"#!/bin/sh\nexec {sort_path} \"$@\"\n")


