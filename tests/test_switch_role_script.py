from pathlib import Path
import subprocess


def test_switch_role_script_includes_datasette_flag():
    script_path = Path(__file__).resolve().parent.parent / "switch-role.sh"
    content = script_path.read_text()
    assert "--datasette" in content


def test_switch_role_script_controls_datasette_service():
    script_path = Path(__file__).resolve().parent.parent / "switch-role.sh"
    content = script_path.read_text()
    assert "datasette-$SERVICE" in content
    assert 'systemctl stop "datasette-$SERVICE"' in content
    assert 'systemctl start "datasette-$SERVICE"' in content


def test_switch_role_script_installs_datasette_package():
    script_path = Path(__file__).resolve().parent.parent / "switch-role.sh"
    content = script_path.read_text()
    assert '"$BASE_DIR/.venv/bin/pip" install datasette' in content


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
        assert result.stdout.strip() == "TestRole"
    finally:
        role_file.unlink(missing_ok=True)
        if not any(lock_dir.iterdir()):
            lock_dir.rmdir()
        log_file = repo_root / "logs" / "switch-role.log"
        if log_file.exists():
            log_file.unlink()
            if not any(log_file.parent.iterdir()):
                log_file.parent.rmdir()
