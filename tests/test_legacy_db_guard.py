from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "helpers" / "legacy_db_guard.py"


def _write_migration(repo_root: Path, app_label: str, migration_name: str) -> None:
    migrations_dir = repo_root / "apps" / app_label / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")
    (migrations_dir / f"{migration_name}.py").write_text("", encoding="utf-8")


def _write_db(path: Path, migrations: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE django_migrations (app varchar(255), name varchar(255))")
        conn.executemany("INSERT INTO django_migrations (app, name) VALUES (?, ?)", migrations)
        conn.commit()
    finally:
        conn.close()


def test_guard_passes_for_known_migration_graph(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "db.sqlite3"
    _write_migration(repo_root, "core", "0001_initial")
    _write_db(db_path, [("core", "0001_initial")])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--repo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_guard_ignores_framework_migrations_outside_project_apps(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "db.sqlite3"
    _write_migration(repo_root, "core", "0001_initial")
    _write_db(db_path, [("auth", "0001_initial"), ("contenttypes", "0001_initial")])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--repo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_guard_fails_for_unknown_legacy_migration_entries(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "db.sqlite3"
    _write_migration(repo_root, "core", "0001_initial")
    _write_db(db_path, [("core", "0001_initial"), ("core", "0099_legacy")])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--repo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Unsupported legacy migration path detected" in result.stderr
    assert "core.0099_legacy" in result.stderr


def test_guard_fails_for_blocked_legacy_app_labels(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "db.sqlite3"
    _write_migration(repo_root, "core", "0001_initial")
    _write_db(db_path, [("recipes", "0012_legacy_state")])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--repo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "recipes.0012_legacy_state" in result.stderr


def test_guard_uses_django_app_labels_from_app_config(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "db.sqlite3"
    _write_migration(repo_root, "sites", "0001_initial")
    (repo_root / "apps" / "sites" / "apps.py").write_text(
        "from django.apps import AppConfig\n\n"
        "class SitesConfig(AppConfig):\n"
        "    name = 'apps.sites'\n"
        "    label = 'pages'\n",
        encoding="utf-8",
    )
    _write_db(db_path, [("pages", "0001_initial")])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--repo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_guard_fails_when_migration_graph_cannot_be_detected(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "db.sqlite3"
    repo_root.mkdir(parents=True, exist_ok=True)
    _write_db(db_path, [("core", "0001_initial")])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--repo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Cannot perform legacy DB guard check." in result.stderr
