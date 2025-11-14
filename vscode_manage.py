import json
import os
import runpy
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable


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


def main(argv=None):
    argv = list(argv or [])
    base_dir = Path(__file__).resolve().parent
    celery_enabled = (base_dir / "locks/celery.lck").exists()
    if "--celery" in argv:
        celery_enabled = True
        argv.remove("--celery")
    if "--no-celery" in argv:
        celery_enabled = False
        argv.remove("--no-celery")

    worker = beat = None
    is_runserver = bool(argv) and argv[0] == "runserver"
    is_debug_session = "DEBUGPY_LAUNCHER_PORT" in os.environ
    if is_runserver:
        if is_debug_session:
            os.environ["DEBUG"] = "1"
        else:
            os.environ.pop("DEBUG", None)
        if "--noreload" not in argv:
            argv.insert(1, "--noreload")
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
        sys.argv = ["manage.py", *argv]
        if is_runserver:
            _run_vscode_runserver(base_dir, argv, is_debug_session)
        else:
            runpy.run_path("manage.py", run_name="__main__")
    finally:
        if worker:
            worker.terminate()
        if beat:
            beat.terminate()


def _run_vscode_runserver(base_dir: Path, argv: Iterable[str], is_debug_session: bool) -> None:
    """Start ``runserver`` with VS Code specific lifecycle handling."""

    session = RunserverSession(base_dir, list(argv), is_debug_session)
    with session:
        if session.should_run_env_refresh:
            _run_env_refresh(base_dir)
        while True:
            try:
                runpy.run_path("manage.py", run_name="__main__")
                return
            except KeyboardInterrupt:
                if session.consume_restart_request():
                    continue
                raise


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
        self.lock_dir = base_dir / "locks"
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
        self._thread.start()
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


if __name__ == "__main__":
    main(sys.argv[1:])
