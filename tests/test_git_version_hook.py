from __future__ import annotations

from pathlib import Path
import shutil
import stat
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


pytestmark = pytest.mark.role("Control")


def test_pre_commit_appends_dev_marker_after_release(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    scripts_src = REPO_ROOT / "scripts"
    shutil.copytree(scripts_src, repo / "scripts")

    version_file = repo / "VERSION"
    version_file.write_text("0.0.1")

    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Dev Example"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", "scripts/git-hooks"], cwd=repo, check=True)

    hook_path = repo / "scripts" / "git-hooks" / "pre-commit"
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

    subprocess.run(["git", "add", "VERSION", "scripts"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Release 0.0.1"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "v0.0.1"], cwd=repo, check=True)

    (repo / "example.txt").write_text("change")
    subprocess.run(["git", "add", "example.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Update revision"], cwd=repo, check=True)

    assert version_file.read_text().strip() == "0.0.1+d"
    version_blob = subprocess.run(
        ["git", "show", "HEAD:VERSION"], cwd=repo, check=True, capture_output=True, text=True
    )
    assert version_blob.stdout.strip() == "0.0.1+d"
