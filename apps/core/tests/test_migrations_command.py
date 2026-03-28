"""Smoke regression tests for the unified migrations management command."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


pytestmark = [pytest.mark.integration, pytest.mark.regression]


def _seed_apps_root(base_dir):
    apps_dir = base_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "__init__.py").write_text(
        '"""Project application packages."""\n', encoding="utf-8"
    )
    return apps_dir


def _seed_app_migrations(apps_dir, app_label):
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


def test_migrations_rebuild_regenerates_initial_migration(monkeypatch, settings, tmp_path):
    """migrations rebuild should clear stale files and regenerate migrations."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    _seed_app_migrations(apps_dir, "catalog")

    stale = apps_dir / "catalog" / "migrations" / "0002_stale.py"
    stale.write_text("# stale\n", encoding="utf-8")
    invoked_labels = []

    def _fake_call_command(name, *args, **kwargs):
        if name != "makemigrations":
            raise AssertionError(f"Unexpected command: {name} {args}")
        invoked_labels.extend(args)
        _seed_app_migrations(apps_dir, "catalog")

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.Command._get_project_app_labels",
        lambda self, _apps_dir: ["catalog"],
    )

    call_command("migrations", "rebuild")

    content = (apps_dir / "catalog" / "migrations" / "0001_initial.py").read_text(
        encoding="utf-8"
    )
    assert not stale.exists()
    assert "BranchTagOperation" not in content
    assert invoked_labels == ["catalog"]
