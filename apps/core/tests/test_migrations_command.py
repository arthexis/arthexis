"""Regression tests for the unified migrations management command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.migrations.exceptions import MigrationSchemaMissing
from django.db.utils import OperationalError


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


def test_migrations_clear_removes_non_init_files(settings, tmp_path):
    """migrations clear should remove migration modules but keep __init__.py files."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    migration_path = _seed_app_migrations(apps_dir, "catalog")

    call_command("migrations", "clear")

    assert not migration_path.exists()
    assert (apps_dir / "catalog" / "migrations" / "__init__.py").exists()


def test_migrations_check_runs_makemigrations_check(monkeypatch):
    """migrations check should forward to Django's dry-run migration check."""

    called: list[tuple[str, tuple, dict]] = []

    def _fake_call_command(name, *args, **kwargs):
        called.append((name, args, kwargs))

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )

    call_command("migrations", "check")

    assert called == [("makemigrations", (), {"check": True, "dry_run": True})]


@pytest.mark.parametrize(
    ("leaf_nodes", "plan", "raised_exception", "expected_error"),
    [
        (
            [("core", "0001_initial")],
            [("core", "0001_initial")],
            None,
            None,
        ),
        ([], [], None, "no pending migrations"),
        (None, None, MigrationSchemaMissing("missing schema"), None),
        (None, None, OperationalError("database unavailable"), None),
    ],
    ids=["pending", "clean", "schema-missing", "operational-error"],
)
def test_migrations_pending(
    monkeypatch, leaf_nodes, plan, raised_exception, expected_error
):
    """migrations pending should report pending, clean, and bootstrap states."""

    class _FakeGraph:
        def leaf_nodes(self):
            return leaf_nodes

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type("Loader", (), {"graph": _FakeGraph()})()
            if raised_exception is not None:
                raise raised_exception

        def migration_plan(self, targets):
            assert targets == leaf_nodes
            return plan

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": object()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    if expected_error is not None:
        with pytest.raises(CommandError, match=expected_error):
            call_command("migrations", "pending")
        return

    call_command("migrations", "pending")


def test_migrations_rebuild_tags_initial_migration(monkeypatch, settings, tmp_path):
    """migrations rebuild should clear, regenerate, and tag initial migrations."""

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


def test_migrations_rebuild_escapes_branch_id(monkeypatch, settings, tmp_path):
    """migrations rebuild should safely encode branch IDs in generated code."""

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

    malicious_branch = '"); import os; os.system("echo pwned"); #'
    call_command("migrations", "rebuild", branch_id=malicious_branch)

    content = (apps_dir / "catalog" / "migrations" / "0001_initial.py").read_text(
        encoding="utf-8"
    )
    assert '\\"' in content
    assert "import os; os.system" in content


def test_migrations_rebuild_accepts_branch_id(monkeypatch, settings, tmp_path):
    """migrations rebuild should call makemigrations during rebuild flow."""

    called: list[tuple[str, tuple, dict]] = []
    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    _seed_app_migrations(apps_dir, "catalog")

    def _fake_call_command(name, *args, **kwargs):
        called.append((name, args, kwargs))

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )

    call_command("migrations", "rebuild", branch_id="branch-legacy")

    assert called
    name, _args, _kwargs = called[0]
    assert name == "makemigrations"


def test_migrations_clear_calls_clear_operation(settings, tmp_path):
    """migrations clear should remove migration files while preserving __init__.py."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    migration_path = _seed_app_migrations(apps_dir, "legacy")

    call_command("migrations", "clear")

    assert not migration_path.exists()
    assert (apps_dir / "legacy" / "migrations" / "__init__.py").exists()


def test_migrations_next_major_rebuild_regenerates_parallel_line(
    monkeypatch, settings, tmp_path
):
    """next-major-rebuild should generate clean migrations in a parallel module."""

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
    tracks_payload = json.loads((tmp_path / "MIGRATION_TRACKS.json").read_text(encoding="utf-8"))
    assert not stale.exists()
    assert "BranchTagOperation" in content
    assert '"major-1.0-base"' in content
    assert tracks_payload["current_line"] == "0.x"
    assert tracks_payload["current_version"] == "0.2.3"
    assert tracks_payload["next_major"]["version"] == "1.0"


def test_migrations_next_major_rebuild_uses_app_labels_for_migration_modules(
    monkeypatch, settings, tmp_path
):
    """next-major-rebuild should key MIGRATION_MODULES by Django app label."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    _seed_app_migrations(apps_dir, "sites")

    class _FakeAppConfig:
        name = "apps.sites"
        label = "pages"
        path = str(apps_dir / "sites")

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.django_apps.get_app_configs",
        lambda: [_FakeAppConfig()],
    )

    def _fake_call_command(name, *args, **kwargs):
        if name != "makemigrations":
            raise AssertionError(f"Unexpected command: {name} {args}")

        migration_modules = dict(settings.MIGRATION_MODULES)
        assert migration_modules["pages"] == "apps.sites.migrations_v1_0"
        assert "sites" not in migration_modules

        generated = apps_dir / "sites" / "migrations_v1_0" / "0001_initial.py"
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


def test_migrations_next_major_rebuild_restores_missing_migration_modules_attr(
    monkeypatch, settings, tmp_path
):
    """next-major-rebuild should restore absent MIGRATION_MODULES settings state."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    del settings.MIGRATION_MODULES
    _seed_app_migrations(apps_dir, "catalog")

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

    assert not hasattr(settings, "MIGRATION_MODULES")


def test_migrations_switch_major_marks_active_line(settings, tmp_path):
    """switch-major should persist the active major migration line."""

    settings.BASE_DIR = tmp_path
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")

    call_command("migrations", "switch-major", major_version="1.0")

    tracks_payload = json.loads(
        (tmp_path / "MIGRATION_TRACKS.json").read_text(encoding="utf-8")
    )
    assert tracks_payload["current_line"] == "1.x"
    assert tracks_payload["current_version"] == "1.0.0"
    assert tracks_payload["next_major"]["status"] == "active"
    assert tracks_payload["next_major"]["module_suffix"] == "migrations_v1_0"
