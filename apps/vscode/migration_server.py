#!/usr/bin/env python3
"""Watch source files and run ``env-refresh`` when changes occur."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import multiprocessing
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable

import psutil

BASE_DIR = Path(__file__).resolve().parents[2]
LOCK_DIR = BASE_DIR / ".locks"
REQUIREMENTS_FILE = Path("requirements.txt")
REQUIREMENTS_HASH_FILE = LOCK_DIR / "requirements.sha256"
PIP_INSTALL_HELPER = Path("scripts") / "helpers" / "pip_install.py"

if importlib.util.find_spec("apps.core.notifications"):
    from apps.core.notifications import notify_async as notify_async  # type: ignore
else:
    def notify_async(subject: str, body: str = "") -> None:
        """Fallback notification when :mod:`core.notifications` is unavailable."""

        print(f"Notification: {subject} - {body}")


WATCH_EXTENSIONS = {
    ".py",
    ".pyi",
    ".html",
    ".htm",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".sass",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".toml",
    ".po",
    ".mo",
    ".txt",
    ".sh",
    ".bat",
}

WATCH_FILENAMES = {
    "Dockerfile",
    "manage.py",
    "pyproject.toml",
    "requirements.txt",
    "env-refresh.py",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    ".idea",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "docs",
    "htmlcov",
    "logs",
    "node_modules",
    "releases",
    "static",
    "tmp",
    ".venv",
}


def _format_elapsed(seconds: float) -> str:
    """Return a short human-readable elapsed time string."""

    return f"{seconds:.2f}s"


def _should_skip_dir(parts: Iterable[str]) -> bool:
    """Return ``True`` when any component in *parts* should be ignored."""

    for part in parts:
        if part in EXCLUDED_DIR_NAMES:
            return True
        if part.startswith(".") and part not in WATCH_FILENAMES:
            return True
    return False


def _should_watch_file(relative_path: Path) -> bool:
    """Return ``True`` when *relative_path* represents a watched file."""

    if relative_path.name in WATCH_FILENAMES:
        return True
    return relative_path.suffix.lower() in WATCH_EXTENSIONS


def collect_source_mtimes(base_dir: Path) -> Dict[str, int]:
    """Return a snapshot of watched files under *base_dir*."""

    snapshot: Dict[str, int] = {}
    for root, dirs, files in os.walk(base_dir):
        rel_root = Path(root).relative_to(base_dir)
        if _should_skip_dir(rel_root.parts):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not _should_skip_dir((*rel_root.parts, d))]
        for name in files:
            rel_path = rel_root / name
            if not _should_watch_file(rel_path):
                continue
            full_path = Path(root, name)
            try:
                snapshot[rel_path.as_posix()] = full_path.stat().st_mtime_ns
            except FileNotFoundError:
                continue
    return snapshot


def diff_snapshots(previous: Dict[str, int], current: Dict[str, int]) -> list[str]:
    """Return a human readable summary of differences between two snapshots."""

    changes: list[str] = []
    prev_keys = set(previous)
    curr_keys = set(current)
    for added in sorted(curr_keys - prev_keys):
        changes.append(f"added {added}")
    for removed in sorted(prev_keys - curr_keys):
        changes.append(f"removed {removed}")
    for common in sorted(prev_keys & curr_keys):
        if previous[common] != current[common]:
            changes.append(f"modified {common}")
    return changes


def build_env_refresh_command(base_dir: Path, *, latest: bool = True) -> list[str]:
    """Return the command used to run ``env-refresh`` from *base_dir*."""

    script = base_dir / "env-refresh.py"
    if not script.exists():
        raise FileNotFoundError("env-refresh.py not found")
    command = [sys.executable, str(script)]
    if latest:
        command.append("--latest")
    command.append("database")
    return command


def run_env_refresh(base_dir: Path, *, latest: bool = True) -> bool:
    """Run env-refresh and return ``True`` when the command succeeds."""

    command = build_env_refresh_command(base_dir, latest=latest)
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print("[Migration Server] Running:", " ".join(command))
    result = subprocess.run(command, cwd=base_dir, env=env)
    if result.returncode != 0:
        notify_async(
            "Migration failure",
            "Check VS Code output for env-refresh details.",
        )
        return False
    return True


def run_env_refresh_with_report(base_dir: Path, *, latest: bool) -> bool:
    """Execute ``env-refresh`` and print a summary of the outcome."""

    started_at = time.monotonic()
    success = run_env_refresh(base_dir, latest=latest)
    elapsed = _format_elapsed(time.monotonic() - started_at)
    if success:
        print(f"[Migration Server] env-refresh completed successfully in {elapsed}.")
        request_runserver_restart(LOCK_DIR)
    else:
        print(
            f"[Migration Server] env-refresh failed after {elapsed}."
            " Awaiting further changes."
        )
    return success


def _backend_port(base_dir: Path, default: int = 8888) -> int:
    """Return the configured backend port with a safe fallback."""

    lock_file = base_dir / ".locks" / "backend_port.lck"
    try:
        raw_value = lock_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
    except OSError:
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default

    if 1 <= value <= 65535:
        return value
    return default


def build_runserver_command(base_dir: Path, *, reload: bool = False) -> list[str]:
    """Return the command used to run the Django development server."""

    manage_py = base_dir / "manage.py"
    if not manage_py.exists():
        raise FileNotFoundError("manage.py not found")

    port = _backend_port(base_dir)
    command = [
        sys.executable,
        str(manage_py),
        "runserver",
        f"127.0.0.1:{port}",
    ]
    if not reload:
        command.append("--noreload")
    return command


def _run_django_server(
    command: list[str], *, cwd: Path | str | None = None, env: dict[str, str] | None = None
) -> None:
    """Execute the Django server command in a child process.

    Designed for use with :class:`multiprocessing.Process` to allow the caller to
    terminate the spawned server via :func:`stop_django_server`.
    """

    resolved_env = os.environ.copy() if env is None else env
    resolved_env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    try:
        process = subprocess.Popen(command, cwd=cwd, env=resolved_env)
        process.wait()
    except OSError as exc:
        print(f"[Migration Server] Failed to run Django server: {exc}")


def _terminate_process_tree(pid: int, *, timeout: float = 5.0) -> None:
    """Terminate a process and its children using :mod:`psutil`."""

    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return

    children = parent.children(recursive=True)

    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            continue

    try:
        parent.terminate()
    except psutil.Error:
        parent = None

    _, alive = psutil.wait_procs(children + ([parent] if parent else []), timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            continue
    if alive:
        psutil.wait_procs(alive, timeout=timeout / 2)


def start_django_server(base_dir: Path, *, reload: bool = False) -> subprocess.Popen | None:
    """Launch the Django server in a child process and return it."""

    try:
        command = build_runserver_command(base_dir, reload=reload)
    except FileNotFoundError as exc:
        print(f"[Migration Server] Unable to start Django server: {exc}")
        return None

    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print("[Migration Server] Starting Django server:", " ".join(command))

    try:
        return subprocess.Popen(command, cwd=base_dir, env=env)
    except OSError as exc:
        print(f"[Migration Server] Failed to start Django server: {exc}")
        return None


def stop_django_server(process: subprocess.Popen | multiprocessing.Process | None) -> None:
    """Terminate the Django server process if it is running."""

    if process is None:
        return

    if isinstance(process, subprocess.Popen):
        if process.poll() is not None:
            return

        print("[Migration Server] Stopping Django server...")
        _terminate_process_tree(process.pid)
        return

    if not process.is_alive():
        return

    print("[Migration Server] Stopping Django server...")
    _terminate_process_tree(process.pid)
    process.join(timeout=0.1)


def _hash_file(path: Path) -> str:
    """Return the sha256 hash of *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_requirements(base_dir: Path) -> bool:
    """Install Python requirements when ``requirements.txt`` changes."""

    req_file = base_dir / REQUIREMENTS_FILE
    hash_file = base_dir / REQUIREMENTS_HASH_FILE
    helper_script = base_dir / PIP_INSTALL_HELPER

    hash_file.parent.mkdir(parents=True, exist_ok=True)

    if not req_file.exists():
        return False

    try:
        current_hash = _hash_file(req_file)
    except OSError:
        return False

    try:
        stored_hash = hash_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        stored_hash = ""
    except OSError:
        stored_hash = ""

    if current_hash == stored_hash:
        return False

    print("[Migration Server] Installing Python requirements...")
    if helper_script.exists():
        command = [sys.executable, str(helper_script), "-r", str(req_file)]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(req_file),
        ]

    result = subprocess.run(command, cwd=base_dir)
    if result.returncode != 0:
        print("[Migration Server] Failed to install Python requirements.")
        notify_async(
            "Python requirements update failed",
            "See migration server output for details.",
        )
        return False

    try:
        hash_file.write_text(current_hash, encoding="utf-8")
    except OSError:
        pass

    print("[Migration Server] Python requirements updated.")
    return True


def wait_for_changes(base_dir: Path, snapshot: Dict[str, int], *, interval: float) -> Dict[str, int]:
    """Block until watched files differ from *snapshot* and return the update."""

    while True:
        time.sleep(max(0.1, interval))
        current = collect_source_mtimes(base_dir)
        if current != snapshot:
            return current


def _is_process_alive(pid: int) -> bool:
    """Return ``True`` if *pid* refers to a running process."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def migration_server_state(lock_dir: Path):
    """Context manager that records the migration server PID."""

    lock_dir.mkdir(parents=True, exist_ok=True)
    state_path = lock_dir / "migration_server.json"

    @contextmanager
    def _manager():
        payload = {"pid": os.getpid(), "timestamp": time.time()}
        try:
            state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass
        try:
            yield state_path
        finally:
            try:
                state_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    return _manager()


def request_runserver_restart(lock_dir: Path) -> None:
    """Signal VS Code run/debug servers to restart after migrations."""

    state_path = lock_dir / "vscode_runserver.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except json.JSONDecodeError:
        return
    pid = payload.get("pid")
    token = payload.get("token")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int) or not _is_process_alive(pid):
        return
    if not isinstance(token, str) or not token:
        return
    restart_path = lock_dir / f"vscode_runserver.restart.{token}"
    try:
        restart_path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        return
    print("[Migration Server] Signalled VS Code run/debug tasks to restart.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run env-refresh whenever source code changes are detected."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval (seconds) before checking for updates.",
    )
    parser.add_argument(
        "--latest",
        dest="latest",
        action="store_true",
        default=True,
        help="Pass --latest to env-refresh (default).",
    )
    parser.add_argument(
        "--no-latest",
        dest="latest",
        action="store_false",
        help="Do not force --latest when invoking env-refresh.",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Sleep for this many seconds after detecting a change to allow batches.",
    )
    args = parser.parse_args(argv)

    update_requirements(BASE_DIR)
    print("[Migration Server] Starting in", BASE_DIR)
    snapshot = collect_source_mtimes(BASE_DIR)
    print("[Migration Server] Watching for changes... Press Ctrl+C to stop.")
    with migration_server_state(LOCK_DIR):
        run_env_refresh_with_report(BASE_DIR, latest=args.latest)
        snapshot = collect_source_mtimes(BASE_DIR)

        try:
            while True:
                updated = wait_for_changes(BASE_DIR, snapshot, interval=args.interval)
                if args.debounce > 0:
                    time.sleep(args.debounce)
                    updated = collect_source_mtimes(BASE_DIR)
                    if updated == snapshot:
                        continue
                if update_requirements(BASE_DIR):
                    notify_async(
                        "New Python requirements installed",
                        "The migration server stopped after installing new dependencies.",
                    )
                    print(
                        "[Migration Server] New Python requirements installed."
                        " Stopping."
                    )
                    return 0
                change_summary = diff_snapshots(snapshot, updated)
                if change_summary:
                    display = "; ".join(change_summary[:5])
                    if len(change_summary) > 5:
                        display += "; ..."
                    print(f"[Migration Server] Changes detected: {display}")
                run_env_refresh_with_report(BASE_DIR, latest=args.latest)
                snapshot = collect_source_mtimes(BASE_DIR)
        except KeyboardInterrupt:
            print("[Migration Server] Stopped.")
            return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
