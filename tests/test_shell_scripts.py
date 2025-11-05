from pathlib import Path
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


SCRIPTS_WITH_HELP = [p for p in REPO_ROOT.glob("*.sh") if "--help" in p.read_text()]


@pytest.mark.parametrize("script_path", SCRIPTS_WITH_HELP)
def test_script_help(script_path: Path) -> None:
    result = subprocess.run(["bash", str(script_path), "--help"], cwd=REPO_ROOT)
    assert result.returncode == 0


def test_backup_restore_roundtrip(prepared_repo: Path) -> None:
    repo = prepared_repo
    sample = repo / "example.log"
    sample.write_text("data")
    archive = repo / "backup.tgz"
    subprocess.run(["bash", "backup-fs.sh", str(archive)], cwd=repo, check=True)
    sample.unlink()
    subprocess.run(["bash", "restore-fs.sh", str(archive)], cwd=repo, check=True)
    assert sample.read_text() == "data"


def test_db_setup_clean_flag(prepared_repo: Path) -> None:
    repo = prepared_repo
    result = subprocess.run(
        ["bash", "db-setup.sh", "--clean"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_upgrade_script_preserves_user_data_dir() -> None:
    script_text = (REPO_ROOT / "upgrade.sh").read_text()
    assert "git clean -fd -e data/" in script_text


@pytest.mark.parametrize(
    "script_name",
    ["install.sh", "upgrade.sh", "start.sh"],
)
def test_primary_scripts_do_not_manage_network_interfaces(script_name: str) -> None:
    script_text = (REPO_ROOT / script_name).read_text()
    forbidden_tokens = ["nmcli", "wlan1-refresh", "wlan1-device-refresh", "NetworkManager"]
    assert all(token not in script_text for token in forbidden_tokens), (
        f"{script_name} should not contain network configuration commands"
    )
