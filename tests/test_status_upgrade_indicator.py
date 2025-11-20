import shutil
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def clone_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, repo)
    return repo


def run_status_script(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "status.sh"], cwd=repo, capture_output=True, text=True, check=False
    )


def write_startup_lock(repo: Path, seconds_ago: int) -> Path:
    lock_file = repo / "locks" / "startup_started_at.lck"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    started_at = int(time.time()) - seconds_ago
    lock_file.write_text(f"{started_at}\nport=8000\n")
    return lock_file


def write_error_log(repo: Path, message: str) -> None:
    logs_dir = repo / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "error.log").write_text(message)


def test_status_reports_upgrade_progress(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    lock_file = repo / "locks" / "upgrade_in_progress.lck"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("2025-01-01T00:00:00+00:00\n")

    result = run_status_script(repo)

    assert (
        "Upgrade status: in progress (started at 2025-01-01T00:00:00+00:00)"
        in result.stdout
    )


def test_status_reports_idle_without_upgrade_lock(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    lock_dir = repo / "locks"
    if lock_dir.exists():
        shutil.rmtree(lock_dir)

    result = run_status_script(repo)

    assert "Upgrade status: idle" in result.stdout


def test_status_shows_startup_in_progress(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    write_startup_lock(repo, seconds_ago=10)

    result = run_status_script(repo)

    assert "Startup in progress: suite not reachable yet" in result.stdout
    assert result.returncode == 0


def test_status_reports_startup_failure_after_timeout(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    write_startup_lock(repo, seconds_ago=400)
    write_error_log(repo, "boom")

    result = run_status_script(repo)

    assert "Startup failed: suite not reachable after 300s." in result.stdout
    assert "boom" in result.stdout
    assert result.returncode == 1
