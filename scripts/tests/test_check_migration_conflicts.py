"""Regression tests for migration conflict pre-checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_migration_conflicts



def _write_migration(path: Path, dependencies: list[tuple[str, str]] | None = None) -> None:
    """Create a migration file with literal dependencies."""

    deps = dependencies or []
    path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        f"    dependencies = {deps!r}\n"
        "    operations = []\n",
        encoding="utf-8",
    )


def test_run_checks_reports_duplicate_leaves_and_naming_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Conflict checks should fail with app/file details for common migration mistakes."""

    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "widgets"
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")

    _write_migration(migrations_dir / "0001_initial.py")
    _write_migration(migrations_dir / "0002_add_status.py", [("widgets", "0001_initial")])
    _write_migration(migrations_dir / "0002_add_color_pr100.py", [("widgets", "0001_initial")])

    exit_code = check_migration_conflicts.run_checks(tmp_path, app_labels={"widgets"})
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "app 'widgets'" in captured.err
    assert "apps/widgets/migrations/0002_add_status.py" in captured.err
    assert "apps/widgets/migrations/0002_add_color_pr100.py" in captured.err


def test_run_checks_passes_for_linear_ticketed_chain(tmp_path: Path) -> None:
    """Conflict checks should pass when naming and graph shape follow policy."""

    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "devices"
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")

    _write_migration(migrations_dir / "0001_initial.py")
    _write_migration(migrations_dir / "0002_add_mode_pr321.py", [("devices", "0001_initial")])
    _write_migration(migrations_dir / "0003_add_state_ticket654.py", [("devices", "0002_add_mode_pr321")])

    assert check_migration_conflicts.run_checks(tmp_path, app_labels={"devices"}) == 0


def test_run_checks_ignores_non_target_apps(tmp_path: Path) -> None:
    """Checks should not scan unrelated apps when explicit targets resolve to none."""

    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "widgets"
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")

    _write_migration(migrations_dir / "0001_initial.py")
    _write_migration(migrations_dir / "0002_add_status.py", [("widgets", "0001_initial")])
    _write_migration(migrations_dir / "0002_add_color.py", [("widgets", "0001_initial")])

    assert check_migration_conflicts.run_checks(tmp_path, app_labels={"not_widgets"}) == 0


def test_run_checks_excludes_replaced_migrations_from_leaf_detection(tmp_path: Path) -> None:
    """Squashed replacements should not be treated as active leaves."""

    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "orders"
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")

    _write_migration(migrations_dir / "0001_initial.py")
    _write_migration(migrations_dir / "0002_add_flag_pr10.py", [("orders", "0001_initial")])
    _write_migration(migrations_dir / "0003_add_note_pr11.py", [("orders", "0002_add_flag_pr10")])
    (migrations_dir / "0004_squashed_0002_0003.py").write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        "    replaces = [('orders', '0002_add_flag_pr10'), ('orders', '0003_add_note_pr11')]\n"
        "    dependencies = [('orders', '0001_initial')]\n"
        "    operations = []\n",
        encoding="utf-8",
    )

    assert check_migration_conflicts.run_checks(tmp_path, app_labels={"orders"}) == 0
