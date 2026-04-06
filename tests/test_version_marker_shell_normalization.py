"""Coverage for shell-based VERSION marker normalization wiring."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_HELPER = REPO_ROOT / "scripts" / "helpers" / "version_marker.sh"


def _run_version_marker_helper(path: Path) -> None:
    cmd = (
        f"source {shlex.quote(str(VERSION_HELPER))} && "
        f"arthexis_update_version_marker {shlex.quote(str(path))}"
    )
    subprocess.run(["bash", "--noprofile", "--norc", "-c", cmd], check=True)


def test_version_marker_shell_ignores_missing_or_empty_version(tmp_path: Path) -> None:
    _run_version_marker_helper(tmp_path)
    assert not (tmp_path / "VERSION").exists()

    (tmp_path / "VERSION").write_text("")
    _run_version_marker_helper(tmp_path)

    assert (tmp_path / "VERSION").read_text() == ""
