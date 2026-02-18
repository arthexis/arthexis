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


pytestmark = [pytest.mark.critical, pytest.mark.regression]


def _write_executable(path: Path, content: str) -> None:
    """Write an executable helper script to *path* with *content*."""

    path.write_text(content)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR)


def _find_bash() -> str:
    """Return a runnable bash executable path for shell-helper tests."""

    bash_from_path = shutil.which("bash")
    if bash_from_path:
        return bash_from_path

    if os.access("/bin/bash", os.X_OK):
        return "/bin/bash"

    pytest.skip("bash is required for scripts/helpers/common.sh tests")


def _isolated_path(fake_bin: Path) -> str:
    """Return a PATH value restricted to the temporary fake binary directory."""

    return bash_path(fake_bin)


def _find_sort() -> str:
    """Return a runnable sort executable path for shell-helper tests."""

    sort_path = shutil.which("sort")
    if sort_path:
        return bash_path(Path(sort_path))

    pytest.skip("'sort' is required for scripts/helpers/common.sh tests")


@pytest.mark.parametrize("binary_name", ["python3", "python"])
def test_arthexis_python_bin_prefers_standard_names(tmp_path: Path, binary_name: str) -> None:
    """The helper should return python3 or python when either is available."""

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / binary_name,
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )

    script = (
        "source scripts/helpers/common.sh\n"
        "arthexis_python_bin\n"
    )
    sort_path = shlex.quote(_find_sort())
    _write_executable(fake_bin / "sort", f"#!/bin/sh\nexec {sort_path} \"$@\"\n")
    env = os.environ | {"PATH": _isolated_path(fake_bin)}
    bash_executable = _find_bash()
    result = subprocess.run(
        [bash_executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == bash_path(fake_bin / binary_name)


def test_arthexis_python_bin_accepts_version_suffixed_python3(tmp_path: Path) -> None:
    """The helper should discover python3.x aliases when python3 is missing."""

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )

    script = (
        "source scripts/helpers/common.sh\n"
        "arthexis_python_bin\n"
    )
    sort_path = shlex.quote(_find_sort())
    _write_executable(fake_bin / "sort", f"#!/bin/sh\nexec {sort_path} \"$@\"\n")
    env = os.environ | {"PATH": _isolated_path(fake_bin)}
    bash_executable = _find_bash()
    result = subprocess.run(
        [bash_executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == bash_path(fake_bin / "python3.12")


def test_arthexis_python_bin_supports_trailing_empty_path_entry(tmp_path: Path) -> None:
    """A trailing ':' in PATH should preserve the implicit current-directory lookup."""

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    workspace_python = tmp_path / "python3"
    _write_executable(
        workspace_python,
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )

    script = (
        f"source {shlex.quote(bash_path(Path(__file__).resolve().parents[1] / 'scripts/helpers/common.sh'))}\n"
        "arthexis_python_bin\n"
    )
    sort_path = shlex.quote(_find_sort())
    _write_executable(fake_bin / "sort", f"#!/bin/sh\nexec {sort_path} \"$@\"\n")
    env = os.environ | {"PATH": f"{_isolated_path(fake_bin)}:"}
    bash_executable = _find_bash()
    result = subprocess.run(
        [bash_executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "./python3"
