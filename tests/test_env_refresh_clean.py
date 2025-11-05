import subprocess
import sys
from pathlib import Path


def test_env_refresh_leaves_repo_clean(prepared_repo: Path) -> None:
    subprocess.run(
        [sys.executable, "env-refresh.py", "--clean"],
        cwd=prepared_repo,
        check=True,
    )

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=prepared_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
