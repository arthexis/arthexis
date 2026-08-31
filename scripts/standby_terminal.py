#!/usr/bin/env python3
"""Manage an isolated standby Terminal checkout for migration cutover tests."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8000
DEFAULT_STATE_DIR = BASE_DIR / "work" / "standby-terminal"
PID_FILE_NAME = "standby-terminal.pid"
STATUS_FILE_NAME = "status.json"


@dataclass(frozen=True)
class StandbyPaths:
    """Filesystem layout for the standby Terminal harness."""

    source_checkout: Path
    state_dir: Path
    checkout: Path
    runtime_dir: Path
    log_dir: Path
    report_dir: Path
    db_path: Path
    test_db_path: Path
    cache_dir: Path
    pid_file: Path
    status_file: Path


@dataclass(frozen=True)
class CommandResult:
    """Captured command result for validation reports."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def resolve_paths(
    *,
    source_checkout: Path = BASE_DIR,
    state_dir: Path = DEFAULT_STATE_DIR,
) -> StandbyPaths:
    """Return the isolated standby filesystem layout."""

    source_checkout = source_checkout.resolve()
    state_dir = state_dir.resolve()
    runtime_dir = state_dir / "runtime"
    return StandbyPaths(
        source_checkout=source_checkout,
        state_dir=state_dir,
        checkout=state_dir / "checkout",
        runtime_dir=runtime_dir,
        log_dir=state_dir / "logs",
        report_dir=state_dir / "reports",
        db_path=runtime_dir / "db.sqlite3",
        test_db_path=runtime_dir / "test_db.sqlite3",
        cache_dir=runtime_dir / "cache",
        pid_file=state_dir / PID_FILE_NAME,
        status_file=state_dir / STATUS_FILE_NAME,
    )


def ensure_state_dirs(paths: StandbyPaths) -> None:
    """Create standby state directories."""

    for path in (
        paths.state_dir,
        paths.runtime_dir,
        paths.log_dir,
        paths.report_dir,
        paths.cache_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a foreground command with text output."""

    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        check=check,
    )


def current_origin_url(source_checkout: Path) -> str:
    """Return the source checkout's origin URL."""

    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=source_checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    remote = result.stdout.strip()
    return remote or "https://github.com/arthexis/arthexis.git"


def ensure_checkout(paths: StandbyPaths, *, remote: str | None, branch: str) -> None:
    """Create or refresh the isolated standby checkout."""

    ensure_state_dirs(paths)
    remote_url = remote or current_origin_url(paths.source_checkout)
    if not (paths.checkout / ".git").exists():
        if paths.checkout.exists():
            raise SystemExit(
                f"Standby checkout path exists but is not a git checkout: {paths.checkout}"
            )
        _run(
            ["git", "clone", "--branch", branch, remote_url, str(paths.checkout)],
            cwd=paths.state_dir,
        )
    _run(["git", "fetch", "origin", branch], cwd=paths.checkout)
    _run(["git", "switch", branch], cwd=paths.checkout)


def write_runtime_locks(paths: StandbyPaths, *, port: int) -> None:
    """Write role and port locks that identify the isolated standby checkout."""

    lock_dir = paths.checkout / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "role.lck").write_text("Terminal\n", encoding="utf-8")
    (lock_dir / "backend_port.lck").write_text(f"{port}\n", encoding="utf-8")
    (lock_dir / "standby-terminal.lck").write_text(
        f"state_dir={paths.state_dir}\n",
        encoding="utf-8",
    )


def runtime_env(paths: StandbyPaths, *, port: int) -> dict[str, str]:
    """Build the environment used by standby install, upgrade, and start commands."""

    env = os.environ.copy()
    env.update(
        {
            "ARTHEXIS_DB_BACKEND": "sqlite",
            "ARTHEXIS_SQLITE_PATH": str(paths.db_path),
            "ARTHEXIS_SQLITE_TEST_PATH": str(paths.test_db_path),
            "ARTHEXIS_LOG_DIR": str(paths.log_dir),
            "DJANGO_CACHE_DIR": str(paths.cache_dir),
            "NODE_ROLE": "Terminal",
            "NET_MESSAGE_DISABLE_PROPAGATION": "1",
            "NODES_ENABLE_SIBLING_IPC": "0",
            "PORT": str(port),
        }
    )
    return env


def _script(paths: StandbyPaths, name: str) -> Path:
    return paths.checkout / name


def install_command(paths: StandbyPaths) -> list[str]:
    """Return the platform-native install command for the standby checkout."""

    if os.name == "nt":
        return [str(_script(paths, "install.bat"))]
    return ["bash", str(_script(paths, "install.sh")), "--terminal", "--embedded"]


def env_refresh_command(paths: StandbyPaths) -> list[str]:
    """Return the platform-native env refresh command."""

    if os.name == "nt":
        return [str(_script(paths, "env-refresh.bat")), "--latest"]
    return ["bash", str(_script(paths, "env-refresh.sh")), "--latest"]


def upgrade_command(paths: StandbyPaths) -> list[str]:
    """Return the platform-native upgrade command."""

    if os.name == "nt":
        return [str(_script(paths, "upgrade.bat")), "--latest"]
    return ["bash", str(_script(paths, "upgrade.sh")), "--latest"]


def start_command(paths: StandbyPaths, *, port: int) -> list[str]:
    """Return the platform-native foreground start command."""

    if os.name == "nt":
        return [str(_script(paths, "start.bat")), "--port", str(port)]
    return ["bash", str(_script(paths, "start.sh")), "--port", str(port), "--await"]


def run_bootstrap(paths: StandbyPaths, *, port: int) -> None:
    """Install or refresh the standby checkout using repo-native commands."""

    write_runtime_locks(paths, port=port)
    env = runtime_env(paths, port=port)
    if os.name == "nt":
        python_path = paths.checkout / ".venv" / "Scripts" / "python.exe"
    else:
        python_path = paths.checkout / ".venv" / "bin" / "python"
    if not python_path.exists():
        _run(install_command(paths), cwd=paths.checkout, env=env)
    else:
        _run(env_refresh_command(paths), cwd=paths.checkout, env=env)


def run_upgrade(paths: StandbyPaths, *, port: int) -> None:
    """Upgrade the standby checkout and refresh its isolated runtime."""

    write_runtime_locks(paths, port=port)
    env = runtime_env(paths, port=port)
    _run(upgrade_command(paths), cwd=paths.checkout, env=env)
    _run(env_refresh_command(paths), cwd=paths.checkout, env=env)


def read_pid(pid_file: Path) -> int | None:
    """Read a pid file."""

    try:
        value = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value.isdigit():
        return None
    return int(value)


def process_is_running(pid: int | None) -> bool:
    """Return whether a process appears to be running."""

    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def probe_http(port: int, *, timeout: float = 2.0) -> int | None:
    """Return the HTTP status for the standby root endpoint when reachable."""

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=timeout
        ) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return None


def write_status(paths: StandbyPaths, payload: dict[str, object]) -> None:
    """Write the latest standby status payload."""

    ensure_state_dirs(paths)
    payload = {"updated_at": datetime.now(UTC).isoformat(), **payload}
    paths.status_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def status_payload(paths: StandbyPaths, *, port: int) -> dict[str, object]:
    """Build a status payload for display and machine reads."""

    pid = read_pid(paths.pid_file)
    running = process_is_running(pid)
    http_status = probe_http(port)
    head = ""
    if (paths.checkout / ".git").exists():
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=paths.checkout,
            capture_output=True,
            text=True,
            check=False,
        )
        head = result.stdout.strip()
    return {
        "state_dir": str(paths.state_dir),
        "checkout": str(paths.checkout),
        "port": port,
        "pid": pid,
        "process_running": running,
        "http_status": http_status,
        "git_head": head,
        "database": str(paths.db_path),
        "logs": str(paths.log_dir),
    }


def wait_for_http(port: int, *, timeout: int) -> bool:
    """Wait for the standby root endpoint to respond."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = probe_http(port)
        if status is not None and status < 500:
            return True
        time.sleep(2)
    return False


def start_standby(paths: StandbyPaths, *, port: int, timeout: int) -> None:
    """Start the standby Terminal process in the background."""

    write_runtime_locks(paths, port=port)
    pid = read_pid(paths.pid_file)
    if process_is_running(pid):
        print(f"Standby Terminal is already running with pid {pid}.")
        return

    ensure_state_dirs(paths)
    log_path = paths.log_dir / "standby-terminal.log"
    log_handle = log_path.open("ab")
    command = start_command(paths, port=port)
    env = runtime_env(paths, port=port)
    popen_kwargs: dict[str, object] = {
        "cwd": paths.checkout,
        "env": env,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    paths.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    write_status(
        paths,
        {
            "command": command,
            "pid": process.pid,
            "port": port,
            "log": str(log_path),
        },
    )
    if not wait_for_http(port, timeout=timeout):
        if process.poll() is not None:
            raise SystemExit(
                f"Standby Terminal exited with status {process.returncode}; see {log_path}"
            )
        raise SystemExit(f"Timed out waiting for http://127.0.0.1:{port}/")
    print(f"Standby Terminal is reachable at http://127.0.0.1:{port}/")


def stop_standby(paths: StandbyPaths, *, timeout: int = 30) -> None:
    """Stop the standby Terminal process tree."""

    pid = read_pid(paths.pid_file)
    if not process_is_running(pid):
        print("Standby Terminal is not running.")
        try:
            paths.pid_file.unlink()
        except OSError:
            pass
        return
    assert pid is not None
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            break
        time.sleep(1)
    try:
        paths.pid_file.unlink()
    except OSError:
        pass
    print("Standby Terminal stopped.")


def validation_commands(paths: StandbyPaths) -> list[list[str]]:
    """Return cutover validation commands for the standby checkout."""

    python_path = (
        paths.checkout / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else paths.checkout / ".venv" / "bin" / "python"
    )
    python_cmd = str(python_path)
    return [
        [python_cmd, "manage.py", "migrations", "check"],
        [python_cmd, "manage.py", "makemigrations", "--check", "--dry-run"],
        [python_cmd, "manage.py", "showmigrations", "--plan", "--skip-checks"],
        [python_cmd, "manage.py", "check"],
    ]


def run_validation(
    paths: StandbyPaths,
    *,
    port: int,
    source_db: Path | None = None,
) -> Path:
    """Run migration cutover validation and write a report."""

    ensure_state_dirs(paths)
    if source_db is not None:
        if not source_db.exists():
            raise SystemExit(f"Source database not found: {source_db}")
        shutil.copy2(source_db, paths.db_path)
    env = runtime_env(paths, port=port)
    _run(env_refresh_command(paths), cwd=paths.checkout, env=env)
    results: list[CommandResult] = []
    for command in validation_commands(paths):
        completed = subprocess.run(
            command,
            cwd=paths.checkout,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            CommandResult(
                command=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        )
    report_path = write_validation_report(paths, results, source_db=source_db)
    failing = [result for result in results if result.returncode != 0]
    if failing:
        raise SystemExit(f"Validation failed; report written to {report_path}")
    return report_path


def write_validation_report(
    paths: StandbyPaths,
    results: list[CommandResult],
    *,
    source_db: Path | None,
) -> Path:
    """Write a markdown report for migration cutover evidence."""

    ensure_state_dirs(paths)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report_path = paths.report_dir / f"cutover-validation-{timestamp}.md"
    lines = [
        "# Standby Terminal Cutover Validation",
        "",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Checkout: `{paths.checkout}`",
        f"- Database: `{paths.db_path}`",
        f"- Source database: `{source_db or 'current standby database'}`",
        "",
        "## Commands",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"### `{' '.join(result.command)}`",
                "",
                f"- Exit code: `{result.returncode}`",
                "",
                "```text",
                result.stdout.strip(),
                "```",
            ]
        )
        if result.stderr.strip():
            lines.extend(["", "```text", result.stderr.strip(), "```"])
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def print_status(paths: StandbyPaths, *, port: int, json_output: bool) -> None:
    """Print standby status."""

    payload = status_payload(paths, port=port)
    if json_output:
        print(json.dumps(payload, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help=f"Standby state root (default: {DEFAULT_STATE_DIR})",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--remote", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ensure", help="Create or refresh the standby checkout.")
    start_parser = subparsers.add_parser("start", help="Start the standby Terminal.")
    start_parser.add_argument("--timeout", type=int, default=300)
    start_parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Skip install/env-refresh before starting.",
    )
    subparsers.add_parser("stop", help="Stop the standby Terminal.")
    restart_parser = subparsers.add_parser(
        "restart", help="Restart the standby Terminal."
    )
    restart_parser.add_argument("--timeout", type=int, default=300)
    status_parser = subparsers.add_parser("status", help="Print standby status.")
    status_parser.add_argument("--json", action="store_true")
    upgrade_parser = subparsers.add_parser(
        "upgrade", help="Upgrade the standby checkout."
    )
    upgrade_parser.add_argument("--start", action="store_true")
    upgrade_parser.add_argument("--timeout", type=int, default=300)
    validate_parser = subparsers.add_parser(
        "validate-cutover",
        help="Run migration cutover validation against the standby database.",
    )
    validate_parser.add_argument("--source-db", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = resolve_paths(state_dir=args.state_dir)
    if args.command == "ensure":
        ensure_checkout(paths, remote=args.remote, branch=args.branch)
        run_bootstrap(paths, port=args.port)
        print(f"Standby checkout ready at {paths.checkout}")
        return 0
    if args.command == "start":
        ensure_checkout(paths, remote=args.remote, branch=args.branch)
        if not args.no_bootstrap:
            run_bootstrap(paths, port=args.port)
        start_standby(paths, port=args.port, timeout=args.timeout)
        return 0
    if args.command == "stop":
        stop_standby(paths)
        return 0
    if args.command == "restart":
        stop_standby(paths)
        ensure_checkout(paths, remote=args.remote, branch=args.branch)
        run_bootstrap(paths, port=args.port)
        start_standby(paths, port=args.port, timeout=args.timeout)
        return 0
    if args.command == "status":
        print_status(paths, port=args.port, json_output=args.json)
        return 0
    if args.command == "upgrade":
        stop_standby(paths)
        ensure_checkout(paths, remote=args.remote, branch=args.branch)
        run_upgrade(paths, port=args.port)
        if args.start:
            start_standby(paths, port=args.port, timeout=args.timeout)
        return 0
    if args.command == "validate-cutover":
        stop_standby(paths)
        ensure_checkout(paths, remote=args.remote, branch=args.branch)
        run_bootstrap(paths, port=args.port)
        report_path = run_validation(paths, port=args.port, source_db=args.source_db)
        print(f"Validation report written to {report_path}")
        return 0
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
