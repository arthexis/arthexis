"""Regression tests for the nmcli setup helper script."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "nmcli-setup.sh"
BASH = shutil.which("bash")


pytestmark = pytest.mark.regression


def _script_path_for_bash(script_path: Path) -> str:
    """Return a script path string that the configured bash executable can read."""
    raw_path = str(script_path)
    if os.name != "nt" or BASH is None:
        return raw_path

    bash_name = Path(BASH).name.lower()
    if bash_name != "bash.exe":
        return raw_path

    drive = script_path.drive.rstrip(":")
    if not drive:
        return script_path.as_posix()

    relative = script_path.as_posix().split(":", maxsplit=1)[1].lstrip("/")
    return f"/mnt/{drive.lower()}/{relative}"


def test_nmcli_setup_script_has_valid_bash_syntax() -> None:
    """The setup script should parse successfully under bash."""
    if BASH is None:
        pytest.skip("bash not found in PATH")

    script_path = _script_path_for_bash(SCRIPT_PATH)
    result = subprocess.run(
        [BASH, "-n", script_path],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_nmcli_setup_script_uses_safe_parsing_patterns() -> None:
    """The script should parse nmcli output robustly and force stable locale."""
    contents = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'LC_ALL=C nmcli --terse --fields NAME,TYPE connection show' in contents
    assert 'connection_type="${line##*:}"' in contents
    assert 'connection_id="${line%:"$connection_type"}"' in contents
    assert 'connection_id="${connection_id//\\\\:/:}"' in contents


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
  if [[ "${2:-}" == "802-11-wireless.mode,connection.interface-name" ]]; then
    case "${6:-}" in
      'Home:Guest')
        printf 'infrastructure\n\n'
        ;;
      'OfficeWiFi')
        printf 'infrastructure\n\n'
        ;;
      *)
        printf 'infrastructure\n\n'
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

    env = {
        "PATH": os.pathsep.join((str(tmp_path), str(Path(BASH).parent))),
        "NMCLI_MOCK_LOG": str(log_path),
    }

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
