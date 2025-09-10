from pathlib import Path
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent


def clone_repo(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, clone_dir)
    return clone_dir


def test_env_refresh_installs_pip(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", repo / ".venv"], check=True
    )
    (repo / "requirements.txt").write_text("")
    # replace env-refresh.py with a no-op to avoid heavy imports
    (repo / "env-refresh.py").write_text("if __name__ == '__main__':\n    pass\n")
    result = subprocess.run(["bash", "env-refresh.sh"], cwd=repo)
    assert result.returncode == 0
    check = subprocess.run(
        [str(repo / ".venv/bin/python"), "-m", "pip", "--version"], cwd=repo
    )
    assert check.returncode == 0
