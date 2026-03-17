"""Tests for the service rename helper script."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.pr_origin(6299)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "rename_service"


def test_rename_service_dry_run_uses_lock_file_name(tmp_path: Path) -> None:
    """Verify dry run resolves the current service from ``.locks/service.lck``."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "service.lck").write_text("alpha\n", encoding="utf-8")
    (lock_dir / "service_mode.lck").write_text("embedded\n", encoding="utf-8")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--base-dir",
            str(tmp_path),
            "--new-name",
            "beta",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Rename: alpha -> beta" in result.stdout
    assert "Mode: embedded" in result.stdout


def test_rename_service_dry_run_rejects_invalid_new_name(tmp_path: Path) -> None:
    """Verify dry run validates service name safety rules."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "service.lck").write_text("alpha\n", encoding="utf-8")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--base-dir",
            str(tmp_path),
            "--new-name",
            "bad/name",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Invalid service name" in result.stderr
