import subprocess
from pathlib import Path


def test_clean_creates_backup(tmp_path):
    base_dir = Path(__file__).resolve().parent.parent
    clone_dir = tmp_path / "clone"
    subprocess.run(["git", "clone", str(base_dir), str(clone_dir)], check=True)

    db_file = clone_dir / "db.sqlite3"
    import sqlite3

    sqlite3.connect(db_file).close()
    (clone_dir / "migrations.md5").write_text("0" * 32)

    subprocess.run(["python", "env-refresh.py", "--clean"], cwd=clone_dir, check=True)

    backups = list((clone_dir / "backups").glob("db.sqlite3.*.bak"))
    assert backups
    version = (clone_dir / "VERSION").read_text().strip()
    rev = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert f"db.sqlite3.{version}.{rev}." in backups[0].name
