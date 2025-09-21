import importlib
import shutil
import subprocess
from types import SimpleNamespace

from pathlib import Path

import scripts.check_migrations as check_migrations

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


def test_check_migrations_allows_merge(tmp_path: Path) -> None:
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
    assert result.returncode == 0
    assert "Merge migrations detected" not in result.stderr


def test_check_migrations_attempts_merge(monkeypatch, capsys) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run_manage(*args: str) -> SimpleNamespace:
        calls.append(args)
        if "--merge" in args:
            return SimpleNamespace(returncode=0, stdout="Merged", stderr="")
        if "--check" in args and len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Conflicting migrations detected; run makemigrations --merge.",
            )
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")

    monkeypatch.setattr(check_migrations, "_run_manage", fake_run_manage)
    result = check_migrations._check_migrations(["core"])

    assert result == 0
    assert calls == [
        ("makemigrations", "core", "--check", "--dry-run", "--noinput"),
        ("makemigrations", "core", "--merge", "--noinput"),
        ("makemigrations", "core", "--check", "--dry-run", "--noinput"),
    ]

    captured = capsys.readouterr()
    assert "Conflicting migrations detected" in captured.err
    assert "Migrations check passed" in captured.out
