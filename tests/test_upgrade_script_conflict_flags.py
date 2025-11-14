import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")


def test_upgrade_script_conflicting_flags() -> None:
    tests_dir = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(dir=tests_dir, prefix="upgrade-script-") as temp_root:
        temp_root_path = Path(temp_root)
        clone_dir = temp_root_path / "repo"
        shutil.copytree(
            REPO_ROOT,
            clone_dir,
            ignore=shutil.ignore_patterns(
                temp_root_path.name,
                ".git",
            ),
        )
        result = subprocess.run(
            ["bash", "upgrade.sh", "--stable", "--latest"],
            cwd=clone_dir,
            capture_output=True,
            text=True,
        )

    assert result.returncode != 0
    assert "--stable cannot be used together with --latest." in result.stderr
