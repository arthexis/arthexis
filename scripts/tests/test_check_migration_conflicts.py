"""Regression tests for migration conflict pre-check helpers."""

from pathlib import Path

import pytest

from scripts import check_migration_conflicts

pytestmark = pytest.mark.regression


def _write_migration(path: Path, dependencies: list[tuple[str, str]]) -> None:
    """Write a minimal migration module with configurable dependencies."""

    path.parent.mkdir(parents=True, exist_ok=True)
    dep_items = ",\n        ".join(repr(dep) for dep in dependencies)
    path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = [\n"
        f"        {dep_items}\n"
        "    ]\n"
        "    operations = []\n",
        encoding="utf-8",
    )


def test_find_leaf_conflicts_reports_duplicate_leaves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apps with two leaves should be reported with app label and file list."""

    repo_root = tmp_path
    app_dir = repo_root / "apps" / "billing" / "migrations"
    _write_migration(app_dir / "0001_initial.py", [])
    _write_migration(app_dir / "0002_alpha_t100.py", [("billing", "0001_initial")])
    _write_migration(app_dir / "0002_beta_t101.py", [("billing", "0001_initial")])

    monkeypatch.setattr(check_migration_conflicts, "REPO_ROOT", repo_root)
    infos = check_migration_conflicts._load_migration_infos()

    issues = check_migration_conflicts._find_leaf_conflicts(infos)

    assert len(issues) == 1
    assert issues[0].app_label == "billing"
    assert issues[0].message == "multiple leaf migrations detected"
    assert set(issues[0].files) == {
        "apps/billing/migrations/0002_alpha_t100.py",
        "apps/billing/migrations/0002_beta_t101.py",
    }


def test_find_parallel_merge_chains_reports_same_number_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge migrations depending on same-number branches should be flagged."""

    repo_root = tmp_path
    app_dir = repo_root / "apps" / "ops" / "migrations"
    _write_migration(app_dir / "0001_initial.py", [])
    _write_migration(app_dir / "0002_alpha_t11.py", [("ops", "0001_initial")])
    _write_migration(app_dir / "0002_beta_t12.py", [("ops", "0001_initial")])
    _write_migration(
        app_dir / "0003_merge_20260228_1212.py",
        [("ops", "0002_alpha_t11"), ("ops", "0002_beta_t12")],
    )

    monkeypatch.setattr(check_migration_conflicts, "REPO_ROOT", repo_root)
    infos = check_migration_conflicts._load_migration_infos()

    issues = check_migration_conflicts._find_parallel_merge_chains(infos)

    assert len(issues) == 1
    assert issues[0].app_label == "ops"
    assert "same migration number" in issues[0].message
    assert "apps/ops/migrations/0003_merge_20260228_1212.py" in issues[0].files


def test_find_naming_issues_requires_ticket_or_pr_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New migrations without ticket/PR suffix should fail naming policy."""

    valid = tmp_path / "apps" / "blog" / "migrations" / "0004_add_index_pr223.py"
    invalid = tmp_path / "apps" / "blog" / "migrations" / "0004_add_index.py"

    monkeypatch.setattr(check_migration_conflicts, "REPO_ROOT", tmp_path)
    issues = check_migration_conflicts._find_naming_issues([valid, invalid])

    assert len(issues) == 1
    assert issues[0].app_label == "blog"
    assert "ticket/PR suffix" in issues[0].message
    assert issues[0].files == ("apps/blog/migrations/0004_add_index.py",)
