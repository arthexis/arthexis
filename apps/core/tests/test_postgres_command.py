from __future__ import annotations

import io
import json
import stat

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands.postgres import Command
from apps.core.models import DatabaseConfig


@pytest.mark.django_db
def test_postgres_command_stores_lock_and_runtime_config(settings, tmp_path, monkeypatch):
    """The command should persist lock configuration and runtime metadata."""

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(Command, "_validate_connection", lambda self, cfg: (True, "ok"))

    stdout = io.StringIO()
    call_command(
        "postgres",
        "--host",
        "db.local",
        "--port",
        "5433",
        "--name",
        "appdb",
        "--user",
        "app",
        stdout=stdout,
    )

    lock_path = tmp_path / ".locks" / "postgres.lck"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    mode = stat.S_IMODE(lock_path.stat().st_mode)
    assert payload["backend"] == "postgres"
    assert payload["host"] == "db.local"
    assert payload["name"] == "appdb"
    assert "password" not in payload
    assert mode == 0o600

    cfg = DatabaseConfig.objects.get(name="appdb", host="db.local")
    assert cfg.last_status_ok is True
    assert cfg.user == "app"


@pytest.mark.django_db
def test_postgres_command_migrate_requires_connectivity(settings, tmp_path, monkeypatch):
    """Migration should error when Postgres validation fails."""

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(Command, "_validate_connection", lambda self, cfg: (False, "boom"))

    with pytest.raises(CommandError):
        call_command("postgres", "--migrate")
