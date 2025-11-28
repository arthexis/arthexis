from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _configure_git_repo(repo_path: Path) -> None:
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"],
        cwd=repo_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI"],
        cwd=repo_path,
        check=True,
    )


def _setup_clone(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)

    clone = tmp_path / "clone"
    shutil.copytree(
        REPO_ROOT,
        clone,
        ignore=shutil.ignore_patterns(".git", "logs", "locks", "backups", "data"),
    )
    subprocess.run(["git", "init"], cwd=clone, check=True)
    _configure_git_repo(clone)
    subprocess.run(["git", "add", "."], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-m", "Initial checkout"], cwd=clone, check=True)
    subprocess.run(["git", "branch", "-M", "work"], cwd=clone, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=clone, check=True)
    subprocess.run(["git", "push", "-u", "origin", "work"], cwd=clone, check=True)
    return clone, remote


def _prepare_test_scripts(base: Path) -> tuple[Path, Path]:
    (base / "locks").mkdir(exist_ok=True)

    venv_bin = base / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    _make_executable(venv_bin / "python", "#!/usr/bin/env bash\nexit 0\n")

    stop_marker = base / "stopped"
    _make_executable(
        base / "stop.sh",
        "#!/usr/bin/env bash\n"
        f"echo ran > '{stop_marker}'\n"
        "exit 0\n",
    )

    env_marker = base / "refreshed"
    _make_executable(
        base / "env-refresh.sh",
        "#!/usr/bin/env bash\n"
        f"echo ran > '{env_marker}'\n"
        "exit 0\n",
    )

    nginx_helper = base / "scripts" / "helpers" / "nginx_maintenance.sh"
    nginx_helper.write_text(
        "arthexis_can_manage_nginx() { return 1; }\n"
        "arthexis_refresh_nginx_maintenance() { return 0; }\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "git",
            "add",
            "stop.sh",
            "env-refresh.sh",
            "scripts/helpers/nginx_maintenance.sh",
        ],
        cwd=base,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Prepare test stubs"],
        cwd=base,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=base, check=True)

    return stop_marker, env_marker


def _write_git_shim(path: Path, marker: Path, branch: str) -> None:
    real_git = shutil.which("git") or "git"
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"REAL_GIT=\"{real_git}\"\n"
        "MARKER=\"${FETCH_REFLOCK_MARKER:-}\"\n"
        "REMOTE=\"${FETCH_REFLOCK_REMOTE:-origin}\"\n"
        f"BRANCH=\"${{FETCH_REFLOCK_BRANCH:-{branch}}}\"\n"
        "if [ \"${SIMULATE_FETCH_REFLOCK:-0}\" = \"1\" ] && [ \"$1\" = \"fetch\" ] && [ -n \"$MARKER\" ] && [ ! -f \"$MARKER\" ]; then\n"
        "  current=\"$($REAL_GIT rev-parse --verify \"refs/remotes/${REMOTE}/${BRANCH}\" 2>/dev/null || echo 0000000000000000000000000000000000000000)\"\n"
        "  echo \"!\\t${current}..deadbeef ${BRANCH} -> ${REMOTE}/${BRANCH} (unable to update local ref)\"\n"
        "  echo \"error: cannot lock ref 'refs/remotes/${REMOTE}/${BRANCH}': is at ${current} but expected deadbeef\" >&2\n"
        "  touch \"$MARKER\"\n"
        "  exit 1\n"
        "fi\n"
        "exec \"$REAL_GIT\" \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")
def test_upgrade_skips_when_versions_match(tmp_path: Path) -> None:
    clone, _ = _setup_clone(tmp_path)
    stop_marker, _ = _prepare_test_scripts(clone)

    result = subprocess.run(
        ["bash", "./upgrade.sh", "--no-restart"],
        cwd=clone,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Already on version" in result.stdout
    assert not stop_marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")
def test_upgrade_stable_skips_patch_release(tmp_path: Path) -> None:
    clone, _ = _setup_clone(tmp_path)
    stop_marker, _ = _prepare_test_scripts(clone)

    version_file = clone / "VERSION"
    version_file.write_text("1.2.3\n", encoding="utf-8")
    subprocess.run(["git", "add", "VERSION"], cwd=clone, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Set base version"],
        cwd=clone,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=clone, check=True)

    version_file.write_text("1.2.4\n", encoding="utf-8")
    subprocess.run(["git", "add", "VERSION"], cwd=clone, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Advance patch"],
        cwd=clone,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=clone, check=True)
    subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=clone, check=True)

    result = subprocess.run(
        ["bash", "./upgrade.sh", "--stable", "--no-restart"],
        cwd=clone,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Stable channel skipping patch-level upgrade" in result.stdout
    assert not stop_marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")
def test_upgrade_rerun_lock_continues_when_versions_match(tmp_path: Path) -> None:
    clone, _ = _setup_clone(tmp_path)
    stop_marker, env_marker = _prepare_test_scripts(clone)

    rerun_lock = clone / "locks" / "upgrade_rerun_required.lck"
    rerun_lock.write_text((clone / "VERSION").read_text(encoding="utf-8"), encoding="utf-8")

    result = subprocess.run(
        ["bash", "./upgrade.sh", "--no-restart"],
        cwd=clone,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "continuing upgrade" in result.stdout
    assert stop_marker.exists()
    assert env_marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")
def test_upgrade_recovers_from_stale_remote_tracking_ref(tmp_path: Path) -> None:
    clone, _ = _setup_clone(tmp_path)
    stop_marker, _ = _prepare_test_scripts(clone)

    git_shim_dir = tmp_path / "bin"
    git_shim_dir.mkdir()
    marker = tmp_path / "fetch_marker"
    _write_git_shim(git_shim_dir / "git", marker, "work")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{git_shim_dir}:{env['PATH']}",
            "SIMULATE_FETCH_REFLOCK": "1",
            "FETCH_REFLOCK_MARKER": str(marker),
            "FETCH_REFLOCK_BRANCH": "work",
        }
    )

    result = subprocess.run(
        ["bash", "./upgrade.sh", "--no-restart"],
        cwd=clone,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert marker.exists()
    assert "Detected stale remote-tracking ref for origin/work" in result.stdout
    assert "Already on version" in result.stdout
    assert not stop_marker.exists()
