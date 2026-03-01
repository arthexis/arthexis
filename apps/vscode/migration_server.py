#!/usr/bin/env python3
"""Watch source files and run ``env-refresh`` when changes occur."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import signal
import shutil
import subprocess
import sys
import sysconfig
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable


def _windows_process_group_kwargs() -> dict[str, int]:
    """Return subprocess kwargs that isolate child processes on Windows."""

    if os.name != "nt":
        return {}

    creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if not creation_flag:
        return {}
    return {"creationflags": creation_flag}


def _posix_process_group_kwargs() -> dict[str, bool]:
    """Return subprocess kwargs that isolate child processes on POSIX."""

    if os.name != "posix":
        return {}
    return {"start_new_session": True}


def _load_psutil() -> Any | None:
    """Import and return :mod:`psutil` when available.

    The VS Code test and migration servers are often started inside fresh
    virtual environments where ``psutil`` can be temporarily unavailable or
    still being installed. Delaying this import keeps module initialization
    responsive and allows callers to use a best-effort fallback.
    """

    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    return psutil


def _terminate_process_without_psutil(pid: int) -> None:
    """Best-effort process termination when :mod:`psutil` is unavailable."""

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                **_windows_process_group_kwargs(),
            )
        except OSError:
            return
        return

    getpgid = getattr(os, "getpgid", None)
    getpgrp = getattr(os, "getpgrp", None)
    killpg = getattr(os, "killpg", None)

    try:
        if getpgid is None or getpgrp is None:
            raise AttributeError
        process_group_id = getpgid(pid)
        current_group_id = getpgrp()
    except (AttributeError, OSError):
        process_group_id = None
        current_group_id = None

    try:
        if (
            process_group_id is not None
            and process_group_id != current_group_id
            and killpg is not None
        ):
            killpg(process_group_id, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return

def resolve_base_dir(
    *, env: dict[str, str] | None = None, cwd: Path | None = None
) -> Path:
    """Resolve the repository root for VS Code tooling."""

    return Path(__file__).resolve().parents[2]


BASE_DIR = resolve_base_dir()
LOCK_DIR = BASE_DIR / ".locks"
MIGRATION_SERVER_LOCK_FILE = "migration_server.json"
MIGRATION_STATUS_IDLE = "idle"
MIGRATION_STATUS_PROCESSING = "processing"
REQUIREMENTS_FILE = Path("requirements.txt")
REQUIREMENTS_HASH_FILE = Path(".locks") / "requirements.sha256"
PIP_INSTALL_HELPER = Path("scripts") / "helpers" / "pip_install.py"
DEBUGGER_INTERRUPT_RETRY_LIMIT = 1


def _safe_print(*values: object, sep: str = " ", end: str = "\n") -> None:
    """Best-effort ``print`` that ignores interrupts while reporting status.

    The migration server can receive a debugger-triggered ``KeyboardInterrupt``
    while it is already inside interrupt handling. Raising a second interrupt
    from a status ``print`` causes an unnecessary traceback in VS Code output.
    """

    try:
        print(*values, sep=sep, end=end)
    except (KeyboardInterrupt, BrokenPipeError, OSError):
        return

def notify_async(subject: str, body: str = "") -> None:
    """No-op notifier for optional VS Code migration-server notifications."""

    _ = (subject, body)


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


def _should_watch_file(relative_path: str) -> bool:
    """Return ``True`` when *relative_path* represents a watched file.

    ``pathlib.Path.name`` can block on some Python 3.13 Windows debug sessions
    when the path object was composed from mixed-style separators. Using plain
    string parsing keeps change detection responsive and platform agnostic.
    """

    file_name = os.path.basename(relative_path)
    if file_name in WATCH_FILENAMES:
        return True
    _root, suffix = os.path.splitext(file_name)
    return suffix.lower() in WATCH_EXTENSIONS


def collect_source_mtimes(base_dir: Path) -> Dict[str, int]:
    """Return a snapshot of watched files under *base_dir*."""

    snapshot: Dict[str, int] = {}
    base_dir_str = os.fspath(base_dir)
    normalized_base = re.sub(r"[\\/]+", "/", base_dir_str).rstrip("/")
    for root, dirs, files in os.walk(base_dir):
        normalized_root = re.sub(r"[\\/]+", "/", root).rstrip("/")
        if normalized_root == normalized_base:
            rel_parts: tuple[str, ...] = ()
        elif normalized_root.startswith(f"{normalized_base}/"):
            rel_parts = tuple(part for part in normalized_root[len(normalized_base) + 1 :].split("/") if part)
        else:
            rel_root = os.path.relpath(root, base_dir_str)
            rel_parts = tuple(part for part in re.split(r"[\\/]", rel_root) if part and part != ".")
        if _should_skip_dir(rel_parts):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not _should_skip_dir((*rel_parts, d))]
        for name in files:
            rel_path = "/".join((*rel_parts, name))
            if not _should_watch_file(rel_path):
                continue
            candidate_paths = [Path(root, name)]
            normalized_fs_root = root.replace("\\", os.sep).replace("/", os.sep)
            normalized_candidate = Path(normalized_fs_root, name)
            if normalized_fs_root != root and normalized_candidate not in candidate_paths:
                candidate_paths.append(normalized_candidate)

            for full_path in candidate_paths:
                try:
                    snapshot[rel_path] = full_path.stat().st_mtime_ns
                    break
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
    # Keep VS Code migration checks responsive on developer machines where
    # PostgreSQL is not running. env-refresh only needs to validate migration
    # state, and SQLite is the expected local fallback backend.
    env.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    _safe_print("[Migration Server] Running:", " ".join(command))
    result = subprocess.run(
        command,
        cwd=base_dir,
        env=env,
        **_windows_process_group_kwargs(),
    )
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
        _safe_print(f"[Migration Server] env-refresh completed successfully in {elapsed}.")
        request_runserver_restart(LOCK_DIR)
    else:
        _safe_print(
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
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=resolved_env,
            **_windows_process_group_kwargs(),
            **_posix_process_group_kwargs(),
        )
        process.wait()
    except OSError as exc:
        print(f"[Migration Server] Failed to run Django server: {exc}")


def _terminate_process_tree(pid: int, *, timeout: float = 5.0) -> None:
    """Terminate a process and its children, using :mod:`psutil` or a fallback."""

    psutil = _load_psutil()
    if psutil is None:
        _terminate_process_without_psutil(pid)
        return

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
        return subprocess.Popen(
            command,
            cwd=base_dir,
            env=env,
            **_windows_process_group_kwargs(),
            **_posix_process_group_kwargs(),
        )
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


def _site_packages_paths() -> list[Path]:
    """Return candidate site-packages paths for the active interpreter."""

    resolved_paths = sysconfig.get_paths()
    candidates: list[Path] = []
    for key in ("purelib", "platlib"):
        resolved = resolved_paths.get(key)
        if resolved:
            candidates.append(Path(resolved))

    if not candidates:
        # Fallback for unusual/embedded environments where sysconfig omits
        # purelib/platlib paths.
        candidates.extend(
            [
                Path(sys.prefix) / "Lib" / "site-packages",
                Path(sys.prefix)
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages",
            ]
        )

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)
    return unique_candidates


def _cleanup_invalid_site_packages_distributions() -> list[Path]:
    """Remove stale temporary package artifacts such as ``~etuptools`` entries."""

    cleaned: list[Path] = []
    for site_packages in _site_packages_paths():
        if not site_packages.exists() or not site_packages.is_dir():
            continue
        try:
            entries = site_packages.iterdir()
        except OSError:
            continue
        for entry in entries:
            name = entry.name.lower()
            if not name.startswith("~"):
                continue
            is_metadata_artifact = name.endswith(".dist-info") or name.endswith(
                ".egg-info"
            )
            normalized_name = name.lstrip("~")
            packaging_tools = ("setuptools", "pip", "wheel")
            is_packaging_tool_artifact = any(
                normalized_name.startswith((tool, tool[1:]))
                for tool in packaging_tools
            )
            if not is_metadata_artifact and not is_packaging_tool_artifact:
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                continue
            cleaned.append(entry)
    return cleaned


def _update_requirements_impl(
    base_dir: Path,
    *,
    prefix: str,
    notify: Callable[[str, str], None],
    notify_body: str,
    swallow_keyboard_interrupt: bool,
) -> bool:
    """Shared requirements installer for VS Code helper servers."""

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

    cleaned_entries = _cleanup_invalid_site_packages_distributions()
    if cleaned_entries:
        print(
            f"{prefix} Removed stale package metadata: "
            + ", ".join(path.name for path in cleaned_entries)
        )

    print(f"{prefix} Installing Python requirements...")
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

    try:
        result = subprocess.run(
            command,
            cwd=base_dir,
            **_windows_process_group_kwargs(),
        )
    except KeyboardInterrupt:
        if not swallow_keyboard_interrupt:
            raise
        print(f"{prefix} Python requirements update cancelled.")
        return False

    if result.returncode != 0:
        print(f"{prefix} Failed to install Python requirements.")
        notify(
            "Python requirements update failed",
            notify_body,
        )
        return False

    try:
        hash_file.write_text(current_hash, encoding="utf-8")
    except OSError:
        pass

    print(f"{prefix} Python requirements updated.")
    return True


def update_requirements(base_dir: Path) -> bool:
    """Install Python requirements when ``requirements.txt`` changes."""

    return _update_requirements_impl(
        base_dir,
        prefix="[Migration Server]",
        notify=notify_async,
        notify_body="See migration server output for details.",
        swallow_keyboard_interrupt=False,
    )


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


def read_migration_server_state(lock_dir: Path) -> dict[str, Any] | None:
    """Return migration server lock details when the recorded process is live.

    Stale lock files are removed automatically once their PID is no longer
    running, which keeps cooperating helper processes from waiting forever.
    """

    state_path = lock_dir / MIGRATION_SERVER_LOCK_FILE
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None

    pid = payload.get("pid")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int) or not _is_process_alive(pid):
        _safe_unlink(state_path)
        return None

    status = payload.get("status")
    if status not in {MIGRATION_STATUS_IDLE, MIGRATION_STATUS_PROCESSING}:
        status = MIGRATION_STATUS_IDLE

    return {
        "pid": pid,
        "status": status,
        "timestamp": payload.get("timestamp"),
    }


def write_migration_server_state(lock_dir: Path, *, pid: int, status: str) -> Path:
    """Persist migration server lock details for cooperating VS Code services."""

    lock_dir.mkdir(parents=True, exist_ok=True)
    state_path = lock_dir / MIGRATION_SERVER_LOCK_FILE
    payload = {
        "pid": pid,
        "status": status,
        "timestamp": time.time(),
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return state_path


def _safe_unlink(path: Path) -> bool:
    """Best-effort file removal that tolerates debugger interrupts.

    During VS Code debugger stop/restart flows on Python 3.13, path string
    coercion can receive a transient ``KeyboardInterrupt`` while shutdown
    handlers run. Cleanup should stay best-effort and never mask the original
    shutdown reason.
    """

    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except KeyboardInterrupt:
        return False
    except OSError:
        return False
    return True


def update_migration_server_status(lock_dir: Path, status: str) -> None:
    """Update the current migration server status if its lock is still valid."""

    state = read_migration_server_state(lock_dir)
    if state is None:
        return
    try:
        write_migration_server_state(lock_dir, pid=state["pid"], status=status)
    except OSError:
        return


def _run_refresh_with_status(base_dir: Path, *, latest: bool) -> bool:
    """Run env-refresh while reflecting processing/idle lock state."""

    update_migration_server_status(LOCK_DIR, MIGRATION_STATUS_PROCESSING)
    success = False
    try:
        success = run_env_refresh_with_report(base_dir, latest=latest)
        return success
    finally:
        if success:
            update_migration_server_status(LOCK_DIR, MIGRATION_STATUS_IDLE)


def migration_server_state(lock_dir: Path):
    """Context manager that records the migration server PID."""

    lock_dir.mkdir(parents=True, exist_ok=True)
    state_path = lock_dir / MIGRATION_SERVER_LOCK_FILE

    @contextmanager
    def _manager():
        try:
            write_migration_server_state(
                lock_dir,
                pid=os.getpid(),
                status=MIGRATION_STATUS_IDLE,
            )
        except OSError:
            pass
        try:
            yield state_path
        finally:
            _safe_unlink(state_path)

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
    _safe_print("[Migration Server] Signalled VS Code run/debug tasks to restart.")


def _is_debugger_session(env: dict[str, str] | None = None) -> bool:
    """Return ``True`` when the process appears to run under a debugger."""

    resolved_env = os.environ if env is None else env
    return bool(
        resolved_env.get("DEBUGPY_LAUNCHER_PORT")
        or resolved_env.get("PYDEVD_LOAD_VALUES_ASYNC")
    )


def main(argv: list[str] | None = None) -> int:
    """Run the migration server event loop."""

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

    try:
        update_requirements(BASE_DIR)
    except KeyboardInterrupt:
        _safe_print(
            "[Migration Server] Stopped after receiving an interrupt signal. "
            "If you did not press Ctrl+C, this likely came from your IDE/debugger "
            "stopping or restarting the session."
        )
        return 0

    _safe_print("[Migration Server] Starting in", BASE_DIR)
    snapshot = collect_source_mtimes(BASE_DIR)
    _safe_print("[Migration Server] Watching for changes... Press Ctrl+C to stop.")
    remaining_interrupt_retries = (
        DEBUGGER_INTERRUPT_RETRY_LIMIT if _is_debugger_session() else 0
    )
    with migration_server_state(LOCK_DIR):
        is_first_run = True
        while True:
            try:
                if is_first_run:
                    _run_refresh_with_status(BASE_DIR, latest=args.latest)
                    snapshot = collect_source_mtimes(BASE_DIR)
                    is_first_run = False

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
                    _safe_print(
                        "[Migration Server] New Python requirements installed."
                        " Stopping."
                    )
                    return 0
                change_summary = diff_snapshots(snapshot, updated)
                if change_summary:
                    display = "; ".join(change_summary[:5])
                    if len(change_summary) > 5:
                        display += "; ..."
                    _safe_print(f"[Migration Server] Changes detected: {display}")
                _run_refresh_with_status(BASE_DIR, latest=args.latest)
                snapshot = collect_source_mtimes(BASE_DIR)
            except KeyboardInterrupt:
                update_migration_server_status(LOCK_DIR, MIGRATION_STATUS_IDLE)
                if remaining_interrupt_retries > 0:
                    remaining_interrupt_retries -= 1
                    _safe_print(
                        "[Migration Server] Ignoring one transient interrupt from "
                        "the IDE/debugger auto-restart handshake."
                    )
                    snapshot = collect_source_mtimes(BASE_DIR)
                    continue
                _safe_print(
                    "[Migration Server] Stopped after receiving an interrupt signal. "
                    "If you did not press Ctrl+C, this likely came from your IDE/debugger "
                    "stopping or restarting the session."
                )
                return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
