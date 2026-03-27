"""Tests for SQLite WAL connection PRAGMA setup."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from django.db import DatabaseError

from apps.core import apps as core_apps


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


def test_connect_sqlite_wal_runtime_pragma_failure_keeps_wal(monkeypatch, caplog):
    """Runtime PRAGMA errors should log warnings and preserve successful WAL setup."""

    receiver = _register_sqlite_wal_receiver(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    connection = _FakeConnection(
        vendor="sqlite",
        fail_on={"PRAGMA synchronous=FULL;"},
    )
    with caplog.at_level("WARNING"):
        receiver(connection=connection)

    assert connection.commands == [
        "PRAGMA journal_mode=WAL;",
        "PRAGMA busy_timeout=60000;",
        "PRAGMA synchronous=FULL;",
    ]
    assert "Failed to apply runtime PRAGMA" in caplog.text
    assert "SQLite WAL setup failed" not in caplog.text
    assert "PRAGMA journal_mode=DELETE;" not in connection.commands


def _register_sqlite_wal_receiver(monkeypatch):
    """Register and return the SQLite WAL signal receiver."""

    captured: dict[str, object] = {}

    def _fake_connect(receiver, **kwargs):
        del kwargs
        captured["receiver"] = receiver

    monkeypatch.setattr("django.apps.apps.ready", True)
    monkeypatch.setattr(
        "django.db.backends.signals.connection_created.connect", _fake_connect
    )
    core_apps._connect_sqlite_wal()
    return captured["receiver"]


@dataclass
class _FakeConnection:
    """Minimal SQLite-like connection used to capture executed statements."""

    vendor: str
    fail_on: set[str] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)

    def cursor(self):
        """Return a context manager that records executed SQL statements."""

        return _FakeCursor(commands=self.commands, fail_on=self.fail_on)


class _FakeCursor:
    """Simple cursor context manager that records SQL executions."""

    def __init__(self, *, commands: list[str], fail_on: set[str]):
        """Initialize a fake cursor with commands capture and failure injection."""

        self._commands = commands
        self._fail_on = fail_on

    def __enter__(self):
        """Enter the context manager and return the cursor."""

        return self

    def __exit__(self, exc_type, exc, tb):
        """Exit the context manager without suppressing exceptions."""

        del exc_type, exc, tb
        return False

    def execute(self, sql: str):
        """Record the SQL statement and raise when configured to emulate failures."""

        self._commands.append(sql)
        if sql in self._fail_on:
            raise DatabaseError(f"simulated SQL execution failure for: {sql}")
