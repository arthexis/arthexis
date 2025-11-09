import os
import tempfile
from pathlib import Path
import subprocess


def _cleanup_switch_role_artifacts(repo_root: Path) -> None:
    log_file = repo_root / "logs" / "switch-role.log"
    if log_file.exists():
        log_file.unlink()
        if not any(log_file.parent.iterdir()):
            log_file.parent.rmdir()


def _cleanup_switch_role_state(repo_root: Path) -> None:
    debug_env = repo_root / "debug.env"
    debug_env.unlink(missing_ok=True)
    redis_env = repo_root / "redis.env"
    redis_env.unlink(missing_ok=True)
    lock_dir = repo_root / "locks"
    if lock_dir.exists():
        for child in lock_dir.iterdir():
            child.unlink(missing_ok=True)
        lock_dir.rmdir()
    _cleanup_switch_role_artifacts(repo_root)


def test_switch_role_script_includes_check_flag():
    script_path = Path(__file__).resolve().parent.parent / "switch-role.sh"
    content = script_path.read_text()
    assert "--check" in content


def test_switch_role_script_check_flag_outputs_role():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "switch-role.sh"
    lock_dir = repo_root / "locks"
    lock_dir.mkdir(exist_ok=True)
    role_file = lock_dir / "role.lck"
    role_file.write_text("TestRole")
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--check"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        assert lines[0] == "Role: TestRole"
        assert any(line.startswith("Auto-upgrade:") for line in lines)
        assert any(line.startswith("Debug:") for line in lines)
    finally:
        role_file.unlink(missing_ok=True)
        if not any(lock_dir.iterdir()):
            lock_dir.rmdir()
        _cleanup_switch_role_artifacts(repo_root)


def test_switch_role_script_debug_flag_writes_env():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "switch-role.sh"
    debug_env = repo_root / "debug.env"
    debug_env.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--debug"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0
        assert debug_env.read_text().strip() == "DEBUG=1"
    finally:
        _cleanup_switch_role_state(repo_root)


def test_switch_role_script_no_debug_flag_writes_zero():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "switch-role.sh"
    debug_env = repo_root / "debug.env"
    debug_env.write_text("DEBUG=1")
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--no-debug"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0
        assert debug_env.read_text().strip() == "DEBUG=0"
    finally:
        _cleanup_switch_role_state(repo_root)


def test_switch_role_terminal_defaults_debug_enabled():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "switch-role.sh"
    debug_env = repo_root / "debug.env"
    debug_env.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--terminal"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0
        assert debug_env.read_text().strip() == "DEBUG=1"
    finally:
        _cleanup_switch_role_state(repo_root)


def test_switch_role_terminal_no_debug_overrides_default():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "switch-role.sh"
    debug_env = repo_root / "debug.env"
    debug_env.write_text("DEBUG=1")
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--terminal", "--no-debug"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0
        assert debug_env.read_text().strip() == "DEBUG=0"
    finally:
        _cleanup_switch_role_state(repo_root)


def test_switch_role_non_terminal_defaults_disable_debug(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "switch-role.sh"
    debug_env = repo_root / "debug.env"
    debug_env.write_text("DEBUG=1")
    fake_bin = Path(tempfile.mkdtemp(dir=str(tmp_path)))
    nginx = fake_bin / "nginx"
    nginx.write_text("#!/usr/bin/env bash\nexit 0\n")
    nginx.chmod(0o755)
    redis_cli = fake_bin / "redis-cli"
    redis_cli.write_text("#!/usr/bin/env bash\nif [ \"$1\" = \"ping\" ]; then\n  echo PONG\n  exit 0\nfi\nexit 0\n")
    redis_cli.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--satellite"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=env,
        )
        assert result.returncode == 0
        assert debug_env.read_text().strip() == "DEBUG=0"
    finally:
        _cleanup_switch_role_state(repo_root)
