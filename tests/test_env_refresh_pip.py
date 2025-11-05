from pathlib import Path
import subprocess
import sys

def test_env_refresh_installs_pip(prepared_repo: Path) -> None:
    repo = prepared_repo
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
