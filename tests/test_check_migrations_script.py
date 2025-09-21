import importlib
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

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


@pytest.fixture
def conflicting_migration_state(monkeypatch: pytest.MonkeyPatch):
    module = importlib.import_module("scripts.check_migrations")
    monkeypatch.setattr(module, "_local_app_labels", lambda: ["core"])
    monkeypatch.setattr(module.django, "setup", lambda: None)

    calls: list[list[str]] = []
    state = {"first": True}

    def fake_run(args: list[str], **kwargs):
        calls.append(args)
        if "--merge" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "--check" in args and "--dry-run" in args:
            if state["first"]:
                state["first"] = False
                error = subprocess.CalledProcessError(
                    1,
                    args,
                    output=(
                        "Conflicting migrations detected; multiple leaf nodes in the migration graph: "
                        "core.0001, core.0002"
                    ),
                    stderr="",
                )
                # ``CalledProcessError`` produced by ``subprocess.run`` exposes both
                # ``output`` and ``stdout`` attributes. Mirror that behaviour so the
                # script can inspect the captured output just like in production.
                error.stdout = error.output  # type: ignore[attr-defined]
                raise error
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return module, calls


def test_conflicting_migrations_trigger_merge(
    conflicting_migration_state: tuple[ModuleType, list[list[str]]]
) -> None:
    module, calls = conflicting_migration_state
    exit_code = module.main()

    assert exit_code == 0
    check_calls = [cmd for cmd in calls if "--check" in cmd and "--dry-run" in cmd]
    merge_calls = [cmd for cmd in calls if "--merge" in cmd]

    assert len(check_calls) == 2
    assert len(merge_calls) == 1
