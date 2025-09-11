from pathlib import Path
import shutil
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def clone_repo(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, clone_dir)
    return clone_dir


SCRIPTS_WITH_HELP = [p for p in REPO_ROOT.glob("*.sh") if "--help" in p.read_text()]


@pytest.mark.parametrize("script_path", SCRIPTS_WITH_HELP)
def test_script_help(script_path: Path) -> None:
    result = subprocess.run(["bash", str(script_path), "--help"], cwd=REPO_ROOT)
    assert result.returncode == 0


def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    sample = repo / "example.log"
    sample.write_text("data")
    archive = repo / "backup.tgz"
    subprocess.run(["bash", "backup-fs.sh", str(archive)], cwd=repo, check=True)
    sample.unlink()
    subprocess.run(["bash", "restore-fs.sh", str(archive)], cwd=repo, check=True)
    assert sample.read_text() == "data"


def test_db_setup_clean_flag(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    result = subprocess.run(
        ["bash", "db-setup.sh", "--clean"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
