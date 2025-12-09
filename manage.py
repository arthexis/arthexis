#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Sequence

from config.loadenv import loadenv
from utils import revision


_RUNSERVER_STARTED_AT: float | None = None


def _resolve_interrupt_main() -> Callable[[], None]:
    """Return a callable that raises ``KeyboardInterrupt`` in the main thread."""

    interrupt = getattr(threading, "interrupt_main", None)
    if interrupt:
        return interrupt

    try:  # pragma: no cover - fallback exercised on specific platforms
        import _thread
    except ImportError:  # pragma: no cover - fallback exercised on specific platforms
        _thread = None

    if _thread is not None and hasattr(_thread, "interrupt_main"):
        return _thread.interrupt_main  # type: ignore[attr-defined]

    if hasattr(signal, "SIGINT"):
        def _send_sigint() -> None:
            os.kill(os.getpid(), signal.SIGINT)

        return _send_sigint

    def _raise_keyboard_interrupt() -> None:
        raise KeyboardInterrupt

    return _raise_keyboard_interrupt


def _print_version(base_dir: Path) -> None:
    ver_path = base_dir / "VERSION"
    version = ver_path.read_text().strip() if ver_path.exists() else ""
    rev_value = revision.get_revision()
    rev_short = rev_value[-6:] if rev_value else ""
    msg = f"Version: v{version}"
    if rev_short:
        msg += f" r{rev_short}"
    print(msg)


def _execute_django(argv: Sequence[str], base_dir: Path) -> None:
    _print_version(base_dir)
    try:
        from django.core.management import execute_from_command_line
        from daphne.management.commands.runserver import (
            Command as DaphneRunserver,
        )
        from django.core.management.commands import runserver as core_runserver

        try:
            from django.contrib.staticfiles.management.commands import (
                runserver as static_runserver,
            )
        except Exception:  # pragma: no cover - optional app
            static_runserver = None

        core_runserver.Command = DaphneRunserver
        if static_runserver is not None:
            static_runserver.Command = DaphneRunserver

        def _suppress_migration_check(*_: object, **__: object) -> list[object]:
            return []

        if os.environ.get("DJANGO_SUPPRESS_MIGRATION_CHECK"):
            core_runserver.Command.check_migrations = _suppress_migration_check
            if static_runserver is not None:
                static_runserver.Command.check_migrations = _suppress_migration_check

        def patched_on_bind(self, server_port):
            original_on_bind(self, server_port)
            host = self.addr or (
                self.default_addr_ipv6 if self.use_ipv6 else self.default_addr
            )
            scheme = "wss" if getattr(self, "ssl_options", None) else "ws"
            for path in ["/ws/echo/", "/<path>/<cid>/"]:
                self.stdout.write(
                    f"WebSocket available at {scheme}://{host}:{server_port}{path}"
                )
            http_scheme = "https" if getattr(self, "ssl_options", None) else "http"
            self.stdout.write(
                f"Admin available at {http_scheme}://{host}:{server_port}/admin/"
            )

            global _RUNSERVER_STARTED_AT
            if _RUNSERVER_STARTED_AT is not None:
                elapsed = time.monotonic() - _RUNSERVER_STARTED_AT
                self.stdout.write(f"Startup completed in {elapsed:.2f}s.")

        original_on_bind = core_runserver.Command.on_bind
        core_runserver.Command.on_bind = patched_on_bind
    except ImportError as exc:  # pragma: no cover - Django bootstrap
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    execute_from_command_line(list(argv))


def _run_env_refresh(base_dir: Path) -> None:
    """Execute ``env-refresh`` in *base_dir* using the local interpreter."""

    command = [sys.executable, str(base_dir / "env-refresh.py"), "--latest", "database"]
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    subprocess.run(command, cwd=base_dir, check=True, env=env)


def _is_process_alive(pid: int) -> bool:
    """Return ``True`` when *pid* refers to a running process."""

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


def _is_migration_server_running(lock_dir: Path) -> bool:
    """Return ``True`` when the migration server lock indicates it is active."""

    state_path = lock_dir / "migration_server.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except json.JSONDecodeError:
        return True
    pid = payload.get("pid")
    if isinstance(pid, int) and _is_process_alive(pid):
        return True
    if isinstance(pid, str) and pid.isdigit() and _is_process_alive(int(pid)):
        return True
    try:
        state_path.unlink()
    except OSError:
        pass
    return False


class RunserverSession:
    """Coordinate run/debug tasks with the migration server."""

    def __init__(
        self,
        base_dir: Path,
        argv: list[str],
        is_debug_session: bool,
        *,
        poll_interval: float = 0.5,
        interrupt_main: Callable[[], None] | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.argv = argv
        self.is_debug_session = is_debug_session
        self.lock_dir = base_dir / ".locks"
        self.poll_interval = max(0.05, poll_interval)
        self._interrupt_main = interrupt_main or _resolve_interrupt_main()
        self._restart_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._token = uuid.uuid4().hex
        self.should_run_env_refresh = False
        self.state_path = self.lock_dir / "vscode_runserver.json"
        self.restart_path = self.lock_dir / f"vscode_runserver.restart.{self._token}"

    def __enter__(self) -> "RunserverSession":
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.should_run_env_refresh = not _is_migration_server_running(self.lock_dir)
        self._write_state()
        self._thread = threading.Thread(target=self._watch_for_restart, daemon=True)
        try:
            self._thread.start()
        except KeyboardInterrupt:
            self._cleanup_files()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval * 4)
        self._cleanup_files()

    def consume_restart_request(self) -> bool:
        """Return ``True`` when a restart request has been queued."""

        if self._restart_event.is_set():
            self._restart_event.clear()
            return True
        return False

    def _write_state(self) -> None:
        payload = {
            "pid": os.getpid(),
            "token": self._token,
            "args": self.argv,
            "debug": self.is_debug_session,
            "timestamp": time.time(),
        }
        try:
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _cleanup_files(self) -> None:
        for path in [self.restart_path, self.state_path]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        # Remove stale restart files from previous sessions to prevent confusion.
        try:
            for stale in self.lock_dir.glob("vscode_runserver.restart.*"):
                if stale != self.restart_path:
                    stale.unlink(missing_ok=True)  # type: ignore[arg-type]
        except AttributeError:
            for stale in self.lock_dir.glob("vscode_runserver.restart.*"):
                if stale != self.restart_path:
                    try:
                        stale.unlink()
                    except OSError:
                        pass

    def _watch_for_restart(self) -> None:
        while not self._stop_event.is_set():
            if self.restart_path.exists():
                try:
                    self.restart_path.unlink()
                except OSError:
                    pass
                self._restart_event.set()
                try:
                    self._interrupt_main()
                except RuntimeError:
                    pass
            time.sleep(self.poll_interval)


def _run_runserver(base_dir: Path, argv: list[str], is_debug_session: bool) -> None:
    global _RUNSERVER_STARTED_AT
    _RUNSERVER_STARTED_AT = time.monotonic()

    session = RunserverSession(base_dir, argv, is_debug_session)
    try:
        with session:
            if session.should_run_env_refresh:
                _run_env_refresh(base_dir)
            while True:
                try:
                    _execute_django(["manage.py", *argv], base_dir)
                    return
                except KeyboardInterrupt:
                    if session.consume_restart_request():
                        continue
                    raise
    except KeyboardInterrupt:
        return
    finally:
        _RUNSERVER_STARTED_AT = None


def main(argv: Sequence[str] | None = None) -> None:
    """Run administrative tasks."""

    base_dir = Path(__file__).resolve().parent
    loadenv()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    args = list(argv or sys.argv[1:])
    celery_enabled = (base_dir / ".locks/celery.lck").exists()
    if "--celery" in args:
        celery_enabled = True
        args.remove("--celery")
    if "--no-celery" in args:
        celery_enabled = False
        args.remove("--no-celery")

    debug_flag = "--debug" in args
    if debug_flag:
        args.remove("--debug")

    worker = beat = None
    is_runserver = bool(args) and args[0] == "runserver"
    is_debug_session = debug_flag or "DEBUGPY_LAUNCHER_PORT" in os.environ
    if is_runserver:
        if is_debug_session:
            os.environ["DEBUG"] = "1"
        else:
            os.environ.pop("DEBUG", None)
        if "--noreload" not in args:
            args.insert(1, "--noreload")
    try:
        if celery_enabled:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "config",
                    "worker",
                    "-l",
                    "info",
                    "--concurrency=2",
                ]
            )
            beat = subprocess.Popen(
                [sys.executable, "-m", "celery", "-A", "config", "beat", "-l", "info"]
            )

        os.environ.pop("DEBUGPY_LAUNCHER_PORT", None)
        if "PYTHONPATH" in os.environ:
            os.environ["PYTHONPATH"] = os.pathsep.join(
                p
                for p in os.environ["PYTHONPATH"].split(os.pathsep)
                if "debugpy" not in p
            )
        sys.argv = ["manage.py", *args]
        if is_runserver:
            _run_runserver(base_dir, args, is_debug_session)
        else:
            _execute_django(sys.argv, base_dir)
    finally:
        if worker:
            worker.terminate()
        if beat:
            beat.terminate()


if __name__ == "__main__":  # pragma: no cover - script entry
    main(sys.argv[1:])
