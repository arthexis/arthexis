"""Regression tests for migration baseline policy tooling."""

from pathlib import Path

import pytest

from scripts import build_migration_baseline as baseline



def _write_migration(path: Path, body: str) -> None:
    """Write a migration file with the required module preamble."""

    path.write_text(
        "from django.db import migrations\n\n" + body,
        encoding="utf-8",
    )


def test_evaluate_app_baseline_excludes_replaced_migrations(tmp_path: Path) -> None:
    """Replaced migrations should not count towards active chain depth."""

    mig_dir = tmp_path / "apps" / "nodes" / "migrations"
    mig_dir.mkdir(parents=True)
    _write_migration(
        mig_dir / "0001_initial.py",
        "class Migration(migrations.Migration):\n    dependencies = []\n    operations = []\n",
    )
    _write_migration(
        mig_dir / "0002_step.py",
        "class Migration(migrations.Migration):\n    dependencies = [('nodes', '0001_initial')]\n    operations = []\n",
    )
    _write_migration(
        mig_dir / "0003_step.py",
        "class Migration(migrations.Migration):\n    dependencies = [('nodes', '0002_step')]\n    operations = []\n",
    )
    _write_migration(
        mig_dir / "0004_squashed_0003_step.py",
        "class Migration(migrations.Migration):\n"
        "    replaces = [('nodes', '0002_step'), ('nodes', '0003_step')]\n"
        "    dependencies = [('nodes', '0001_initial')]\n"
        "    operations = []\n",
    )

    status = baseline.evaluate_app_baseline("nodes", repo_root=tmp_path)

    assert status.active_chain_depth == 2
    assert status.latest_number == 4
    assert status.latest_squash_number == 4
    assert status.squash_target == "0001_initial"


def test_validate_squashed_file_rejects_stale_dependency(tmp_path: Path) -> None:
    """Generated squash files must not depend on migrations they replace."""

    squashed = tmp_path / "0005_squashed_0004_step.py"
    _write_migration(
        squashed,
        "class Migration(migrations.Migration):\n"
        "    replaces = [('nodes', '0004_step')]\n"
        "    dependencies = [('nodes', '0004_step')]\n"
        "    operations = []\n",
    )

    with pytest.raises(baseline.MigrationBaselineError, match="stale local dependencies"):
        baseline._validate_squashed_file("nodes", squashed)


def test_validate_squashed_file_rejects_manual_copy_marker(tmp_path: Path) -> None:
    """Squash output requiring manual data migration copy should fail validation."""

    squashed = tmp_path / "0005_squashed_0004_step.py"
    _write_migration(
        squashed,
        "# Functions from the following migrations need manual copying.\n"
        "class Migration(migrations.Migration):\n"
        "    replaces = [('nodes', '0004_step')]\n"
        "    dependencies = [('nodes', '0001_initial')]\n"
        "    operations = []\n",
    )

    with pytest.raises(baseline.MigrationBaselineError, match="manual data-migration copying"):
        baseline._validate_squashed_file("nodes", squashed)
