"""Tests for log directory selection helpers."""

from __future__ import annotations

from pathlib import Path
import os

import pytest

from core import log_paths


@pytest.fixture(autouse=True)
def _clear_log_dir_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARTHEXIS_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)


def test_select_log_dir_handles_missing_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Selecting the log directory should fall back when ``Path.home`` is unavailable."""

    def _raise_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(log_paths, "_is_root", lambda: False)
    monkeypatch.setattr(log_paths.Path, "home", staticmethod(_raise_home))

    chosen = log_paths.select_log_dir(tmp_path)

    expected = tmp_path / "logs"
    assert chosen == expected
    assert expected.exists()
    assert os.environ["ARTHEXIS_LOG_DIR"] == str(expected)
