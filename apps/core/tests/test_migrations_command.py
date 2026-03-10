"""Regression tests for the unified migrations management command."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management import call_command


def _seed_apps_root(base_dir: Path) -> Path:
    apps_dir = base_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "__init__.py").write_text('"""Project application packages."""\n', encoding="utf-8")
    settings.BASE_DIR = base_dir
    settings.APPS_DIR = apps_dir
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


def test_migrations_clear_removes_non_init_files(tmp_path):
    """migrations clear should remove migration modules but keep __init__.py files."""

    apps_dir = _seed_apps_root(tmp_path)
    migration_path = _seed_app_migrations(apps_dir, "catalog")

    call_command("migrations", "clear")

    assert not migration_path.exists()
    assert (apps_dir / "catalog" / "migrations" / "__init__.py").exists()


def test_migrations_rebuild_tags_initial_migration(monkeypatch, tmp_path):
    """migrations rebuild should clear, regenerate, and tag initial migrations."""

    apps_dir = _seed_apps_root(tmp_path)
    _seed_app_migrations(apps_dir, "catalog")

    def _fake_call_command(name, *args, **kwargs):
        if name == "migrations" and args and args[0] == "clear":
            call_command(name, *args, **kwargs)
            return

        if name == "makemigrations":
            _seed_app_migrations(apps_dir, "catalog")
            return

        raise AssertionError(f"Unexpected command: {name} {args}")

    monkeypatch.setattr("apps.core.management.commands.migrations.call_command", _fake_call_command)

    call_command("migrations", "rebuild", branch_id="branch-123")

    content = (apps_dir / "catalog" / "migrations" / "0001_initial.py").read_text(encoding="utf-8")
    assert "BranchTagOperation" in content
    assert '"branch-123"' in content


def test_rebuild_apps_migrations_delegates_to_root_command(monkeypatch):
    """Legacy rebuild_apps_migrations should delegate to migrations rebuild."""

    called: list[tuple[str, tuple, dict]] = []

    def _fake_call_command(name, *args, **kwargs):
        called.append((name, args, kwargs))

    monkeypatch.setattr(
        "apps.core.management.commands.rebuild_apps_migrations.call_command", _fake_call_command
    )

    call_command("rebuild_apps_migrations", branch_id="branch-legacy")

    assert called
    name, args, _kwargs = called[0]
    assert name == "migrations"
    assert args[0] == "rebuild"
    assert "--branch-id" in args


def test_clear_apps_migrations_delegates_to_root_command(monkeypatch):
    """Legacy clear_apps_migrations should delegate to migrations clear."""

    called: list[tuple[str, tuple, dict]] = []

    def _fake_call_command(name, *args, **kwargs):
        called.append((name, args, kwargs))

    monkeypatch.setattr(
        "apps.core.management.commands.clear_apps_migrations.call_command", _fake_call_command
    )

    call_command("clear_apps_migrations")

    assert called
    name, args, _kwargs = called[0]
    assert name == "migrations"
    assert args[0] == "clear"
