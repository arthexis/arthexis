from __future__ import annotations

import os
from pathlib import Path

import pytest

import conftest


def test_set_writable_sqlite_env_replaces_unwritable_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fallback SQLite paths should replace caller config when directory is not writable."""

    fallback = tmp_path / "fallback.sqlite3"
    monkeypatch.setattr(conftest, "_sqlite_path_is_writable", lambda _: False)
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", "/readonly/test.sqlite3")

    conftest._set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", fallback)

    assert os.environ["ARTHEXIS_SQLITE_PATH"] == str(fallback)


def test_set_writable_sqlite_env_preserves_writable_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Writable caller-provided SQLite paths should remain unchanged."""

    configured = tmp_path / "configured.sqlite3"
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", str(configured))

    conftest._set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", tmp_path / "fallback.sqlite3")

    assert os.environ["ARTHEXIS_SQLITE_PATH"] == str(configured)


@pytest.mark.parametrize("configured", [":memory:", "file:memdb1?mode=memory&cache=shared"])
def test_set_writable_sqlite_env_preserves_special_sqlite_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured: str,
) -> None:
    """SQLite in-memory and URI names should not be replaced by path fallback logic."""

    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", configured)

    conftest._set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", tmp_path / "fallback.sqlite3")

    assert os.environ["ARTHEXIS_SQLITE_PATH"] == configured
