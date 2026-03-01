"""Regression tests for migration conflict pre-checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_migration_conflicts

pytestmark = pytest.mark.regression


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


def test_parse_assignment_tuples_rejects_non_literal_entries(tmp_path: Path) -> None:
    """Non-literal dependencies/replaces should fail parsing instead of being ignored."""

    migration_path = tmp_path / "bad_migration.py"
    migration_path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = dynamic_dependencies()\n"
        "    operations = []\n",
        encoding="utf-8",
    )

    with pytest.raises(check_migration_conflicts.MigrationParseError):
        check_migration_conflicts._parse_dependencies(migration_path)


def test_parse_dependencies_allows_swappable_dependency(tmp_path: Path) -> None:
    """Django swappable dependencies should be accepted and ignored for local graph checks."""

    migration_path = tmp_path / "migration_with_swappable.py"
    migration_path.write_text(
        "from django.conf import settings\n"
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = [\n"
        "        migrations.swappable_dependency(settings.AUTH_USER_MODEL),\n"
        "        ('widgets', '0001_initial'),\n"
        "    ]\n"
        "    operations = []\n",
        encoding="utf-8",
    )

    assert check_migration_conflicts._parse_dependencies(migration_path) == [
        ("widgets", "0001_initial")
    ]


def test_git_changed_app_labels_falls_back_to_local_migrations_when_merge_base_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When merge-base cannot be determined, discover labels from local migrations."""

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    apps_dir = tmp_path / "apps"
    widgets_migrations = apps_dir / "widgets" / "migrations"
    widgets_migrations.mkdir(parents=True)
    (widgets_migrations / "__init__.py").write_text("", encoding="utf-8")
    _write_migration(widgets_migrations / "0001_initial.py")

    metrics_migrations = apps_dir / "metrics" / "migrations"
    metrics_migrations.mkdir(parents=True)
    (metrics_migrations / "__init__.py").write_text("", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> Result:
        return Result(returncode=1, stderr="no merge base")

    monkeypatch.setattr(check_migration_conflicts.subprocess, "run", fake_run)

    assert check_migration_conflicts._git_changed_app_labels(tmp_path) == {"widgets"}


def test_git_changed_app_labels_uses_head_diff_when_merge_base_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When merge-base is unavailable, prefer commit-level changed migration paths."""

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args: list[str], **_kwargs: object) -> Result:
        if args[:3] == ["git", "merge-base", "HEAD"]:
            return Result(returncode=1, stderr="no merge base")
        if args[:3] == ["git", "diff-tree", "--name-only"]:
            return Result(
                returncode=0,
                stdout=(
                    "apps/features/migrations/0024_merge_20260228_1838.py\n"
                    "apps/nodes/migrations/0035_nodefeature_footprint.py\n"
                ),
            )
        raise AssertionError(f"Unexpected git invocation: {args}")

    monkeypatch.setattr(check_migration_conflicts.subprocess, "run", fake_run)

    assert check_migration_conflicts._git_changed_app_labels(tmp_path) == {"features", "nodes"}


def test_git_changed_app_labels_handles_non_directory_apps_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fallback scan should fail open when ``apps`` exists but is not a directory."""

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    (tmp_path / "apps").write_text("not a directory", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> Result:
        return Result(returncode=1, stderr="no merge base")

    monkeypatch.setattr(check_migration_conflicts.subprocess, "run", fake_run)

    assert check_migration_conflicts._git_changed_app_labels(tmp_path) == set()
