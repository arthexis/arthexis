from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.plugins import sqlite_paths


def test_set_writable_sqlite_env_replaces_unwritable_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fallback SQLite paths should replace caller config when directory is not writable."""

    fallback = tmp_path / "fallback.sqlite3"
    monkeypatch.setattr(sqlite_paths, "sqlite_path_is_writable", lambda _: False)
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", "/readonly/test.sqlite3")

    sqlite_paths.set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", fallback)

    assert os.environ["ARTHEXIS_SQLITE_PATH"] == str(fallback)


def test_set_writable_sqlite_env_preserves_writable_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Writable caller-provided SQLite paths should remain unchanged."""

    configured = tmp_path / "configured.sqlite3"
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", str(configured))

    sqlite_paths.set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", tmp_path / "fallback.sqlite3")

    assert os.environ["ARTHEXIS_SQLITE_PATH"] == str(configured)


@pytest.mark.parametrize("configured", [":memory:", "file:memdb1?mode=memory&cache=shared"])
def test_set_writable_sqlite_env_preserves_special_sqlite_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured: str,
) -> None:
    """SQLite in-memory and URI names should not be replaced by path fallback logic."""

    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", configured)

    sqlite_paths.set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", tmp_path / "fallback.sqlite3")

    assert os.environ["ARTHEXIS_SQLITE_PATH"] == configured


@pytest.mark.regression
def test_pytest_worker_suffix_defaults_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker suffix should default to ``main`` outside pytest-xdist workers."""

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    assert sqlite_paths.pytest_worker_suffix() == "main"


@pytest.mark.regression
def test_pytest_worker_suffix_uses_xdist_worker_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker suffix should match pytest-xdist worker id when available."""

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    assert sqlite_paths.pytest_worker_suffix() == "gw3"
