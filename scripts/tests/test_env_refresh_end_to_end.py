import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from django.conf import settings


def test_env_refresh_end_to_end():
    base_dir = Path(settings.BASE_DIR)
    locks_dir = base_dir / ".locks"
    temp_state_dir = base_dir / ".tmp_envrefresh"
    temp_state_dir.mkdir(exist_ok=True)
    database_path = temp_state_dir / "test_db.sqlite3"

    if locks_dir.exists():
        shutil.rmtree(locks_dir)
    if database_path.exists():
        database_path.unlink()

    env = os.environ.copy()
    env["ARTHEXIS_SQLITE_PATH"] = str(database_path)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    refresh_command = [
        sys.executable,
        str(base_dir / "env-refresh.py"),
        "--latest",
        "database",
    ]
    refresh_result = subprocess.run(
        refresh_command,
        cwd=base_dir,
        env=env,
        capture_output=True,
        text=True,
    )

    assert refresh_result.returncode == 0, refresh_result.stderr
    assert "Fixtures unchanged" not in refresh_result.stdout

    migrations_hash = locks_dir / "migrations.md5"
    fixtures_hash = locks_dir / "fixtures.md5"
    assert migrations_hash.exists() and migrations_hash.read_text().strip()
    assert fixtures_hash.exists() and fixtures_hash.read_text().strip()

    with sqlite3.connect(database_path) as conn:
        applied_migrations = conn.execute(
            "SELECT COUNT(*) FROM django_migrations"
        ).fetchone()[0]
        assert applied_migrations > 0

        site_count = conn.execute(
            "SELECT COUNT(*) FROM django_site"
        ).fetchone()[0]
        assert site_count >= 1

        node_count = conn.execute(
            "SELECT COUNT(*) FROM nodes_node"
        ).fetchone()[0]
        assert node_count >= 1

        sigil_count = conn.execute(
            "SELECT COUNT(*) FROM core_sigilroot"
        ).fetchone()[0]
        assert sigil_count >= 1

    makemigrations_cmd = [
        sys.executable,
        "manage.py",
        "makemigrations",
        "--check",
        "--dry-run",
    ]
    makemigrations_result = subprocess.run(
        makemigrations_cmd,
        cwd=base_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    assert (
        makemigrations_result.returncode == 0
    ), makemigrations_result.stderr or makemigrations_result.stdout

    shutil.rmtree(locks_dir, ignore_errors=True)
    shutil.rmtree(temp_state_dir, ignore_errors=True)
