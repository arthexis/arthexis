"""Coverage for shell-based VERSION marker normalization wiring."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_HELPER = REPO_ROOT / "scripts" / "helpers" / "version_marker.sh"


def _run_version_marker_helper(path: Path) -> None:
    cmd = (
        f"source {shlex.quote(str(VERSION_HELPER))} && "
        f"arthexis_update_version_marker {shlex.quote(str(path))}"
    )
    subprocess.run(["bash", "--noprofile", "--norc", "-c", cmd], check=True)


@pytest.mark.parametrize(
    ("raw_version", "expected"),
    [
        ("1.2.3+d", "1.2.3\n"),
        ("1.2.3+", "1.2.3\n"),
        ("1.2.3", "1.2.3\n"),
    ],
)
def test_version_marker_shell_normalizes_legacy_suffixes(
    tmp_path: Path,
    raw_version: str,
    expected: str,
) -> None:
    (tmp_path / "VERSION").write_text(raw_version)

    _run_version_marker_helper(tmp_path)

    assert (tmp_path / "VERSION").read_text() == expected


def test_version_marker_shell_ignores_missing_or_empty_version(tmp_path: Path) -> None:
    _run_version_marker_helper(tmp_path)
    assert not (tmp_path / "VERSION").exists()

    (tmp_path / "VERSION").write_text("")
    _run_version_marker_helper(tmp_path)

    assert (tmp_path / "VERSION").read_text() == ""


def test_install_and_upgrade_scripts_wire_version_marker_helper() -> None:
    install_content = (REPO_ROOT / "install.sh").read_text()
    upgrade_content = (REPO_ROOT / "upgrade.sh").read_text()

    assert '. "$SCRIPT_DIR/scripts/helpers/version_marker.sh"' in install_content
    assert 'arthexis_update_version_marker "$BASE_DIR"' in install_content

    assert '. "$BASE_DIR/scripts/helpers/version_marker.sh"' in upgrade_content
    assert 'arthexis_update_version_marker "$BASE_DIR"' in upgrade_content
