"""Tests for SQLite WAL connection PRAGMA setup."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from apps.core import apps as core_apps


@pytest.mark.pr_origin(9999)
def test_connect_sqlite_wal_default_path_unchanged(monkeypatch):
    """WAL setup should keep core PRAGMAs and apply safe defaults."""

    receiver = _register_sqlite_wal_receiver(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    connection = _FakeConnection(vendor="sqlite")
    receiver(connection=connection)

    assert connection.commands == [
        "PRAGMA journal_mode=WAL;",
        "PRAGMA busy_timeout=60000;",
        "PRAGMA synchronous=FULL;",
    ]


@pytest.mark.pr_origin(9999)
def test_connect_sqlite_wal_executes_env_configured_pragmas(monkeypatch):
    """Valid SQLite PRAGMA environment values should be applied on connect."""

    receiver = _register_sqlite_wal_receiver(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ARTHEXIS_SQLITE_SYNCHRONOUS", "normal")
    monkeypatch.setenv("ARTHEXIS_SQLITE_CACHE_SIZE", "-2000")
    monkeypatch.setenv("ARTHEXIS_SQLITE_MMAP_SIZE", "1048576")

    connection = _FakeConnection(vendor="sqlite")
    receiver(connection=connection)

    assert connection.commands == [
        "PRAGMA journal_mode=WAL;",
        "PRAGMA busy_timeout=60000;",
        "PRAGMA synchronous=NORMAL;",
        "PRAGMA cache_size=-2000;",
        "PRAGMA mmap_size=1048576;",
    ]


@pytest.mark.pr_origin(9999)
def test_connect_sqlite_wal_ignores_invalid_env_values_with_warning(monkeypatch, caplog):
    """Invalid PRAGMA env values should be ignored without breaking startup."""

    receiver = _register_sqlite_wal_receiver(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ARTHEXIS_SQLITE_SYNCHRONOUS", "OFF")
    monkeypatch.setenv("ARTHEXIS_SQLITE_CACHE_SIZE", "not-a-number")
    monkeypatch.setenv("ARTHEXIS_SQLITE_MMAP_SIZE", "-5")

    connection = _FakeConnection(vendor="sqlite")
    with caplog.at_level("WARNING"):
        receiver(connection=connection)

    assert connection.commands == [
        "PRAGMA journal_mode=WAL;",
        "PRAGMA busy_timeout=60000;",
        "PRAGMA synchronous=FULL;",
    ]
    assert "Invalid ARTHEXIS_SQLITE_SYNCHRONOUS value" in caplog.text
    assert "Invalid ARTHEXIS_SQLITE_CACHE_SIZE value" in caplog.text
    assert "Invalid ARTHEXIS_SQLITE_MMAP_SIZE value" in caplog.text


def _register_sqlite_wal_receiver(monkeypatch):
    """Register and return the SQLite WAL signal receiver."""

    captured: dict[str, object] = {}

    def _fake_connect(receiver, **kwargs):
        del kwargs
        captured["receiver"] = receiver

    monkeypatch.setattr("django.db.backends.signals.connection_created.connect", _fake_connect)
    core_apps._connect_sqlite_wal()
    return captured["receiver"]


@dataclass
class _FakeConnection:
    """Minimal SQLite-like connection used to capture executed statements."""

    vendor: str
    commands: list[str] = field(default_factory=list)

    def cursor(self):
        """Return a context manager that records executed SQL statements."""

        return _FakeCursor(self.commands)


class _FakeCursor:
    """Simple cursor context manager that records SQL executions."""

    def __init__(self, commands: list[str]):
        self._commands = commands

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def execute(self, sql: str):
        """Record the SQL statement for later assertions."""

        self._commands.append(sql)
