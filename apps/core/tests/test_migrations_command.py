"""Regression tests for the unified migrations management command."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command


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
