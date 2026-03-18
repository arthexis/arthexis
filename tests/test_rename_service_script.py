"""High-level tests for the service rename helper script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.pr_origin(6271)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "rename_service"


def test_rename_service_dry_run_exits_cleanly(tmp_path: Path) -> None:
    """Verify dry-run succeeds and reports the expected rename plan."""

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


@pytest.mark.integration
def test_rename_service_dry_run_respects_disabled_lcd(tmp_path: Path) -> None:
    """Verify dry-run output respects disabled LCD feature flags."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "service.lck").write_text("alpha\n", encoding="utf-8")
    (lock_dir / "lcd_screen.lck").write_text("state=disabled\n", encoding="utf-8")

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
    assert "lcd=false" in result.stdout


@pytest.mark.integration
def test_rename_service_dry_run_infers_systemd_from_old_units(tmp_path: Path) -> None:
    """Verify dry-run infers systemd mode and companions from existing old units."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)

    (systemd_dir / "alpha.service").write_text("[Unit]\n", encoding="utf-8")
    (systemd_dir / "celery-alpha.service").write_text("[Unit]\n", encoding="utf-8")
    (systemd_dir / "lcd-alpha.service").write_text("[Unit]\n", encoding="utf-8")
    (systemd_dir / "rfid-alpha.service").write_text("[Unit]\n", encoding="utf-8")
    (systemd_dir / "camera-alpha.service").write_text("[Unit]\n", encoding="utf-8")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--base-dir",
            str(tmp_path),
            "--old-name",
            "alpha",
            "--new-name",
            "beta",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "SYSTEMD_DIR": str(systemd_dir)},
    )

    assert result.returncode == 0
    assert "Mode: systemd" in result.stdout
    assert "celery=true" in result.stdout
    assert "lcd=true" in result.stdout
    assert "rfid=true" in result.stdout
    assert "camera=true" in result.stdout


@pytest.mark.integration
def test_rename_service_dry_run_rejects_invalid_new_name(tmp_path: Path) -> None:
    """Verify dry-run validates and rejects unsafe service names."""

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
