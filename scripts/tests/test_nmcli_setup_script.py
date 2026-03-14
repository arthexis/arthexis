"""Regression tests for the nmcli setup helper script."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "nmcli-setup.sh"
BASH = shutil.which("bash")




def _script_path_for_bash(script_path: Path) -> str:
    """Return a script path string that the configured bash executable can read."""
    raw_path = str(script_path)
    if os.name != "nt" or BASH is None:
        return raw_path

    bash_path = Path(BASH)
    bash_name = bash_path.name.lower()
    if bash_name != "bash.exe":
        return raw_path

    # Only WSL interop bash (under System32/SysWOW64) uses /mnt/<drive>/...
    # Git Bash (MSYS2) under Program Files\Git\bin expects /<drive>/... paths.
    if bash_path.parent.name.lower() not in ("system32", "syswow64"):
        return script_path.as_posix()

    drive = script_path.drive.rstrip(":")
    if len(drive) != 1 or not drive.isalpha():
        return script_path.as_posix()

    relative = script_path.as_posix().split(":", maxsplit=1)[1].lstrip("/")
    return f"/mnt/{drive.lower()}/{relative}"


def _run_script_with_nmcli_mock(tmp_path: Path, nmcli_mock_contents: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run nmcli-setup with a supplied nmcli mock script and return result and call log."""
    if BASH is None:
        pytest.skip("bash not found in PATH")

    log_path = tmp_path / "calls.log"
    nmcli_mock = tmp_path / "nmcli"
    nmcli_mock.write_text(nmcli_mock_contents, encoding="utf-8")
    nmcli_mock.chmod(0o755)

    bash_parent = Path(BASH).parent
    path_parts = (_script_path_for_bash(tmp_path), _script_path_for_bash(bash_parent))
    path_sep = ":" if (os.name == "nt" and bash_parent.name.lower() in ("system32", "syswow64")) else os.pathsep
    env = {
        **os.environ,
        "PATH": path_sep.join(path_parts),
        "NMCLI_MOCK_LOG": _script_path_for_bash(log_path),
    }
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)

    script_path = _script_path_for_bash(SCRIPT_PATH)
    result = subprocess.run(
        [BASH, script_path],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    calls = log_path.read_text(encoding="utf-8")
    return result, calls


def test_nmcli_setup_script_handles_colons_and_continues_on_profile_failure(tmp_path: Path) -> None:
    """The script should unescape escaped colons and continue after per-profile errors."""
    if BASH is None:
        pytest.skip("bash not found in PATH")

    log_path = tmp_path / "calls.log"
    nmcli_mock = tmp_path / "nmcli"
    nmcli_mock.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
LOG_FILE=${NMCLI_MOCK_LOG:?}
printf '%s\n' "$*" >> "$LOG_FILE"

if [[ "${1:-}" == "--terse" ]]; then
  printf 'Home\\:Guest:wifi\n'
  printf 'BadEth:ethernet\n'
  printf 'OfficeWiFi:wifi\n'
  exit 0
fi

if [[ "${1:-}" == "--get-values" ]]; then
  if [[ "${2:-}" == "802-11-wireless.mode,connection.interface-name,ipv4.method" ]]; then
    case "${6:-}" in
      'Home:Guest')
        printf 'infrastructure\n\nauto\n'
        ;;
      'OfficeWiFi')
        printf 'infrastructure\n\nauto\n'
        ;;
      *)
        printf 'infrastructure\n\nauto\n'
        ;;
    esac
    exit 0
  fi

  if [[ "${2:-}" == "ipv4.method" ]]; then
    if [[ "${5:-}" == "BadEth" ]]; then
      exit 10
    fi
    printf 'shared\n'
    exit 0
  fi
fi

if [[ "${1:-}" == "connection" && "${2:-}" == "modify" ]]; then
  exit 0
fi

if [[ "${1:-}" == "connection" && "${2:-}" == "show" ]]; then
  exit 0
fi

exit 0
""",
        encoding="utf-8",
    )
    nmcli_mock.chmod(0o755)

    bash_parent = Path(BASH).parent
    path_parts = (_script_path_for_bash(tmp_path), _script_path_for_bash(bash_parent))
    path_sep = ":" if (os.name == "nt" and bash_parent.name.lower() in ("system32", "syswow64")) else os.pathsep
    env = {
        **os.environ,
        "PATH": path_sep.join(path_parts),
        "NMCLI_MOCK_LOG": _script_path_for_bash(log_path),
    }
    # Keep ambient env for Windows bash startup expectations, but clear shell hooks
    # so the regression test remains hermetic in CI/dev environments.
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)

    script_path = _script_path_for_bash(SCRIPT_PATH)
    result = subprocess.run(
        [BASH, script_path],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING: failed to configure ethernet profile 'BadEth'; skipping." in result.stdout
    assert "Wi-Fi client profile 'Home:Guest' pinned to wlan0." in result.stdout

    calls = log_path.read_text(encoding="utf-8")
    assert "connection modify Home:Guest" in calls
    assert "connection modify OfficeWiFi" in calls


def test_nmcli_setup_treats_shared_wifi_profile_as_ap_role(tmp_path: Path) -> None:
    """A wifi profile with ipv4 shared should be pinned to the AP interface."""
    result, calls = _run_script_with_nmcli_mock(
        tmp_path,
        """#!/usr/bin/env bash
set -euo pipefail
LOG_FILE=${NMCLI_MOCK_LOG:?}
printf '%s\n' "$*" >> "$LOG_FILE"

if [[ "${1:-}" == "--terse" ]]; then
  printf 'arthexis-ap:wifi\n'
  exit 0
fi

if [[ "${1:-}" == "--get-values" && "${2:-}" == "802-11-wireless.mode,connection.interface-name,ipv4.method" ]]; then
  printf 'infrastructure\n'
  printf '\n'
  printf 'shared\n'
  exit 0
fi

if [[ "${1:-}" == "connection" && "${2:-}" == "modify" ]]; then
  exit 0
fi

exit 0
""",
    )

    assert result.returncode == 0, result.stderr
    assert "AP profile 'arthexis-ap' pinned to wlan1 (mode='infrastructure', ipv4.method='shared')" in result.stdout
    assert "connection modify arthexis-ap connection.interface-name wlan1 connection.autoconnect yes ipv4.method shared" in calls
