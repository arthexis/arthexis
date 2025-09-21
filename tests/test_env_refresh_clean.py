import subprocess
from pathlib import Path


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
