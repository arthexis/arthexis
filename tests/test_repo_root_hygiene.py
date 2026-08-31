from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked_paths() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("root hygiene checks require a git checkout")
    return tuple(path.strip() for path in result.stdout.splitlines() if path.strip())


def test_root_does_not_track_example_templates() -> None:
    root_examples = [
        path
        for path in _tracked_paths()
        if "/" not in path and Path(path).name.lower().endswith(".example")
    ]

    assert root_examples == []


def test_root_does_not_track_backup_artifacts() -> None:
    tracked_backups = [
        path for path in _tracked_paths() if path == "backups" or path.startswith("backups/")
    ]

    assert tracked_backups == []
