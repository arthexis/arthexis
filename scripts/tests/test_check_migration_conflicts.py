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






