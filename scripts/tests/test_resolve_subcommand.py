"""Regression tests for resolve entrypoint compatibility."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.regression,
    pytest.mark.pr("PR-5652", "2026-02-26T00:00:00Z"),
]


REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ENTRYPOINT = REPO_ROOT / "resolve.sh"
NEW_ENTRYPOINT = [sys.executable, "-m", "arthexis", "resolve"]


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    """Create a fake Python executable that captures argv and stdin."""
    fake_python_path = tmp_path / "fake-python"
    fake_python_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf 'ARGV:%s\\n' \"$*\"\n"
        "stdin_value=$(cat)\n"
        "printf 'STDIN:%s\\n' \"$stdin_value\"\n",
        encoding="utf-8",
    )
    fake_python_path.chmod(fake_python_path.stat().st_mode | stat.S_IEXEC)
    return fake_python_path


def _legacy_command() -> list[str]:
    """Return a cross-platform invocation for the legacy resolve shell entrypoint."""

    if os.name == "nt":
        return ["bash", str(LEGACY_ENTRYPOINT)]
    return [str(LEGACY_ENTRYPOINT)]


def _run_command(
    command: list[str],
    fake_python: Path,
    tmp_path: Path,
    stdin_text: str = "",
    *,
    cwd: Path = REPO_ROOT,
    include_fake_arthexis: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a resolve entrypoint with a deterministic fake Python interpreter."""
    env = dict(os.environ)
    env["PYTHON"] = str(fake_python)

    path_prefix = str(tmp_path)
    if include_fake_arthexis:
        fake_arthexis = tmp_path / "arthexis"
        fake_arthexis.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"exec {sys.executable} -m arthexis \"$@\"\n",
            encoding="utf-8",
        )
        fake_arthexis.chmod(fake_arthexis.stat().st_mode | stat.S_IEXEC)

    env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=stdin_text,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("legacy_args", "new_args", "stdin_text"),
    [
        (["--file", "template.txt"], ["--file", "template.txt"], ""),
        (["--text", "Hello [SYS.version]"], ["--text", "Hello [SYS.version]"], ""),
        (["hello", "world"], ["hello", "world"], ""),
        ([], [], "raw [SYS.version] stdin"),
    ],
)
def test_resolve_entrypoints_are_argument_equivalent(
    fake_python: Path,
    tmp_path: Path,
    legacy_args: list[str],
    new_args: list[str],
    stdin_text: str,
) -> None:
    """Legacy and new resolve commands should forward identical resolver invocations."""
    legacy_result = _run_command([*_legacy_command(), *legacy_args], fake_python, tmp_path, stdin_text=stdin_text)
    new_result = _run_command([*NEW_ENTRYPOINT, *new_args], fake_python, tmp_path, stdin_text=stdin_text)

    assert legacy_result.returncode == 0
    assert new_result.returncode == 0
    assert legacy_result.stdout == new_result.stdout
    assert legacy_result.stderr == new_result.stderr


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--file"],
        ["--text"],
        ["--text", "one", "--text", "two"],
        ["--file", "a.txt", "extra"],
    ],
)
def test_resolve_entrypoints_preserve_error_messages(fake_python: Path, tmp_path: Path, bad_args: list[str]) -> None:
    """Both entrypoints should preserve resolve.sh validation errors exactly."""
    legacy_result = _run_command([*_legacy_command(), *bad_args], fake_python, tmp_path)
    new_result = _run_command([*NEW_ENTRYPOINT, *bad_args], fake_python, tmp_path)

    assert legacy_result.returncode == 1
    assert new_result.returncode == 1
    assert legacy_result.stdout == new_result.stdout
    assert legacy_result.stderr == new_result.stderr


def test_resolve_help_text_is_equivalent(fake_python: Path, tmp_path: Path) -> None:
    """The compatibility shim should preserve the legacy help output."""
    legacy_result = _run_command([*_legacy_command(), "--help"], fake_python, tmp_path)
    new_result = _run_command([*NEW_ENTRYPOINT, "--help"], fake_python, tmp_path)

    assert legacy_result.returncode == 0
    assert new_result.returncode == 0
    assert legacy_result.stdout == new_result.stdout
    assert legacy_result.stderr == new_result.stderr


def test_resolve_shim_works_from_any_working_directory(fake_python: Path, tmp_path: Path) -> None:
    """The compatibility shim should work via absolute path outside the repo cwd."""
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()

    result = _run_command(
        [*_legacy_command(), "--text", "hello"],
        fake_python,
        tmp_path,
        cwd=outside_cwd,
        include_fake_arthexis=False,
    )

    assert result.returncode == 0
    assert "ARGV:-m arthexis resolve --text hello" in result.stdout
