"""High-level tests for the service rename helper script."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.pr_origin(6271)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "rename_service"


def test_rename_service_dry_run_exits_cleanly(tmp_path: Path) -> None:
    """Verify the CLI dry-run command succeeds with minimal setup."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "service.lck").write_text("alpha\n", encoding="utf-8")

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
