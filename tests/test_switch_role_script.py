from pathlib import Path
import subprocess


def _cleanup_switch_role_artifacts(repo_root: Path) -> None:
    log_file = repo_root / "logs" / "switch-role.log"
    if log_file.exists():
        log_file.unlink()
        if not any(log_file.parent.iterdir()):
            log_file.parent.rmdir()


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
        debug_env.unlink(missing_ok=True)
        _cleanup_switch_role_artifacts(repo_root)


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
        debug_env.unlink(missing_ok=True)
        _cleanup_switch_role_artifacts(repo_root)
