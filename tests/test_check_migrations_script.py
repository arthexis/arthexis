from pathlib import Path
import shutil
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent


def clone_repo(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, clone_dir)
    return clone_dir


def test_check_migrations_passes() -> None:
    result = subprocess.run(
        ["python", "scripts/check_migrations.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_check_migrations_fails_on_merge(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    merge_file = repo / "core" / "migrations" / "0012_merge_fake.py"
    merge_file.parent.mkdir(parents=True, exist_ok=True)
    merge_file.write_text(
        """from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_businesspowerlead"),
        ("core", "0009_merge_20250901_2230"),
    ]
    operations = []
"""
    )
    result = subprocess.run(
        ["python", "scripts/check_migrations.py"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Merge migrations detected" in result.stderr
