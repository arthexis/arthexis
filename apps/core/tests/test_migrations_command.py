"""Smoke regression tests for the unified migrations management command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


pytestmark = [pytest.mark.integration, pytest.mark.regression]
MIGRATIONS_FILENAME = "MIGRATIONS.json"


def _seed_apps_root(base_dir: Path) -> Path:
    apps_dir = base_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "__init__.py").write_text(
        '"""Project application packages."""\n', encoding="utf-8"
    )
    return apps_dir


def _seed_app_migrations(apps_dir: Path, app_label: str) -> Path:
    migrations_dir = apps_dir / app_label / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")
    migration_path = migrations_dir / "0001_initial.py"
    migration_path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        "    operations = [\n"
        "    ]\n",
        encoding="utf-8",
    )
    return migration_path


def test_migrations_pending_reports_clean_state(monkeypatch):
    """migrations pending should fail closed when no pending work exists."""

    class _FakeGraph:
        def leaf_nodes(self):
            return []

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type("Loader", (), {"graph": _FakeGraph()})()

        def migration_plan(self, targets):
            assert targets == []
            return []

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": object()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    with pytest.raises(CommandError, match="no pending migrations"):
        call_command("migrations", "pending")


def test_migrations_rebuild_tags_initial_migration(monkeypatch, settings, tmp_path):
    """migrations rebuild should tag initial migrations with the branch id."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    _seed_app_migrations(apps_dir, "catalog")

    def _fake_call_command(name, *args, **kwargs):
        if name == "makemigrations":
            _seed_app_migrations(apps_dir, "catalog")
            return

        raise AssertionError(f"Unexpected command: {name} {args}")

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )

    call_command("migrations", "rebuild", branch_id="branch-123")

    content = (apps_dir / "catalog" / "migrations" / "0001_initial.py").read_text(
        encoding="utf-8"
    )
    assert "BranchTagOperation" in content
    assert '"branch-123"' in content


def test_migrations_next_major_rebuild_regenerates_parallel_line(
    monkeypatch, settings, tmp_path
):
    """next-major-rebuild should regenerate parallel-line migrations and track metadata."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    (tmp_path / "VERSION").write_text("0.2.3\n", encoding="utf-8")
    _seed_app_migrations(apps_dir, "catalog")
    (apps_dir / "catalog" / "migrations_v1_0").mkdir(parents=True, exist_ok=True)
    (apps_dir / "catalog" / "migrations_v1_0" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    stale = apps_dir / "catalog" / "migrations_v1_0" / "0007_stale.py"
    stale.write_text("stale", encoding="utf-8")

    def _fake_call_command(name, *args, **kwargs):
        if name != "makemigrations":
            raise AssertionError(f"Unexpected command: {name} {args}")
        generated = apps_dir / "catalog" / "migrations_v1_0" / "0001_initial.py"
        generated.write_text(
            "from django.db import migrations\n\n"
            "class Migration(migrations.Migration):\n"
            "    operations = [\n"
            "    ]\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )

    call_command("migrations", "next-major-rebuild", major_version="1.0")

    content = (apps_dir / "catalog" / "migrations_v1_0" / "0001_initial.py").read_text(
        encoding="utf-8"
    )
    tracks_payload = json.loads(
        (tmp_path / MIGRATIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert not stale.exists()
    assert "BranchTagOperation" in content
    assert '"major-1.0-base"' in content
    assert tracks_payload["next_major"]["version"] == "1.0"


def test_migrations_next_major_rebuild_reads_legacy_tracks_file(
    monkeypatch, settings, tmp_path
):
    """next-major-rebuild should read legacy tracking metadata before writing new file."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    (tmp_path / "VERSION").write_text("0.2.3\n", encoding="utf-8")
    _seed_app_migrations(apps_dir, "catalog")
    (tmp_path / "MIGRATION_TRACKS.json").write_text(
        '{"next_major": {"version": "2.0"}}\n',
        encoding="utf-8",
    )

    def _fake_call_command(name, *args, **kwargs):
        if name != "makemigrations":
            raise AssertionError(f"Unexpected command: {name} {args}")
        generated = apps_dir / "catalog" / "migrations_v2_0" / "0001_initial.py"
        generated.write_text(
            "from django.db import migrations\n\n"
            "class Migration(migrations.Migration):\n"
            "    operations = [\n"
            "    ]\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )

    call_command("migrations", "next-major-rebuild")

    tracks_payload = json.loads(
        (tmp_path / MIGRATIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert tracks_payload["next_major"]["version"] == "2.0"
    assert not (tmp_path / "MIGRATION_TRACKS.json").exists()
