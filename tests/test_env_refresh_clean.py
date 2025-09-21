import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace


def test_env_refresh_leaves_repo_clean(tmp_path):
    base_dir = Path(__file__).resolve().parent.parent
    clone_dir = tmp_path / "clone"
    subprocess.run(["git", "clone", str(base_dir), str(clone_dir)], check=True)

    subprocess.run(["python", "env-refresh.py", "--clean"], cwd=clone_dir, check=True)

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=clone_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""


def test_env_refresh_stashes_merge_migrations(monkeypatch, capsys):
    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "env_refresh_stash", repo_root / "env-refresh.py"
    )
    env_refresh = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(env_refresh)

    monkeypatch.setattr(
        env_refresh,
        "_merge_migration_paths",
        lambda base_dir: ["core/migrations/9999_merge_auto.py"],
    )

    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(env_refresh.subprocess, "run", fake_run)

    env_refresh._stash_merge_migrations(
        repo_root, reason="Conflicting migrations detected"
    )

    assert run_calls == [
        [
            "git",
            "stash",
            "push",
            "--include-untracked",
            "-m",
            "env-refresh merge migrations",
            "--",
            "core/migrations/9999_merge_auto.py",
        ]
    ]

    captured = capsys.readouterr()
    assert "due to conflicting migrations" in captured.out
