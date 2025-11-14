from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_upgrade_clean_prompt_respects_user_response(tmp_path) -> None:
    clone_path = tmp_path / "arthexis-clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", str(REPO_ROOT), str(clone_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    venv_bin = clone_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    _make_executable(venv_bin / "python", "#!/usr/bin/env bash\nexit 0\n")

    _make_executable(clone_path / "stop.sh", "#!/usr/bin/env bash\nexit 0\n")
    _make_executable(clone_path / "env-refresh.sh", "#!/usr/bin/env bash\nexit 0\n")

    nginx_helper = clone_path / "scripts" / "helpers" / "nginx_maintenance.sh"
    nginx_helper.write_text(
        "arthexis_can_manage_nginx() { return 1; }\n"
        "arthexis_refresh_nginx_maintenance() { return 0; }\n",
        encoding="utf-8",
    )

    (clone_path / "VERSION").write_text("0.0.0\n", encoding="utf-8")

    real_git = shutil.which("git")
    assert real_git is not None
    _make_executable(
        clone_path / "git",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"pull\" ] && [ \"$2\" = \"--rebase\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        f"exec {real_git} \"$@\"\n",
    )

    db_path = clone_path / "db.sqlite3"
    db_path.write_text("placeholder", encoding="utf-8")
    extra_db_path = clone_path / "db_extra.sqlite3"
    extra_db_path.write_text("extra", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{clone_path}:{env['PATH']}"

    first_proc = subprocess.Popen(
        ["bash", "./upgrade.sh", "--clean", "--no-restart"],
        cwd=clone_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    first_stdout, _ = first_proc.communicate("n\n")

    assert first_proc.returncode == 1
    assert db_path.exists()
    assert extra_db_path.exists()
    assert "Warning: Running upgrade with --clean will delete" in first_stdout
    assert "Use --no-warn to bypass this prompt." in first_stdout
    assert "Upgrade aborted by user." in first_stdout

    second_result = subprocess.run(
        ["bash", "./upgrade.sh", "--clean", "--no-restart", "--no-warn"],
        cwd=clone_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=env,
    )

    assert second_result.returncode == 0
    assert not db_path.exists()
    assert not extra_db_path.exists()
    assert "Warning: Running upgrade with --clean will delete" not in second_result.stdout
    assert "Use --no-warn to bypass this prompt." not in second_result.stdout
