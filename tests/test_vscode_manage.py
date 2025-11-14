import json
import os
import runpy
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
import vscode_manage


def test_wrapper_strips_debugpy(monkeypatch):
    monkeypatch.setenv("DEBUGPY_LAUNCHER_PORT", "1234")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/a", "/debugpy", "/b"]))
    _patch_session(monkeypatch, should_run_env_refresh=False)

    called = {}
    monkeypatch.setattr(
        runpy, "run_path", lambda path, run_name: called.setdefault("path", path)
    )

    vscode_manage.main(["runserver"])

    assert called["path"] == "manage.py"
    assert "DEBUGPY_LAUNCHER_PORT" not in os.environ
    assert "/debugpy" not in os.environ["PYTHONPATH"]
    assert os.environ["DEBUG"] == "1"
    assert sys.argv == ["manage.py", "runserver", "--noreload"]


def test_wrapper_does_not_set_debug_env_without_debugger(monkeypatch):
    monkeypatch.delenv("DEBUGPY_LAUNCHER_PORT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    _patch_session(monkeypatch, should_run_env_refresh=False)

    called = {}
    monkeypatch.setattr(
        runpy, "run_path", lambda path, run_name: called.setdefault("path", path)
    )

    monkeypatch.setattr(sys, "argv", ["python"])

    vscode_manage.main(["runserver"])

    assert called["path"] == "manage.py"
    assert "DEBUG" not in os.environ
    assert sys.argv == ["manage.py", "runserver", "--noreload"]


def test_wrapper_adds_noreload_for_debug_sessions(monkeypatch):
    monkeypatch.setenv("DEBUGPY_LAUNCHER_PORT", "1234")
    _patch_session(monkeypatch, should_run_env_refresh=False)

    monkeypatch.setattr(runpy, "run_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["python"])

    vscode_manage.main(["runserver", "0.0.0.0:8000"])

    assert sys.argv == ["manage.py", "runserver", "--noreload", "0.0.0.0:8000"]


class _DummySession:
    def __init__(
        self,
        base_dir: Path,
        argv: list[str],
        is_debug_session: bool,
        *,
        should_run_env_refresh: bool,
        restart_requests: list[bool] | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.argv = argv
        self.is_debug_session = is_debug_session
        self.should_run_env_refresh = should_run_env_refresh
        self._restart_requests = list(restart_requests or [])

    def __enter__(self) -> "_DummySession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def consume_restart_request(self) -> bool:
        if self._restart_requests:
            return self._restart_requests.pop(0)
        return False


def _patch_session(monkeypatch, *, should_run_env_refresh: bool, restart_requests=None):
    def _factory(base_dir: Path, argv: list[str], is_debug_session: bool):
        return _DummySession(
            base_dir,
            argv,
            is_debug_session,
            should_run_env_refresh=should_run_env_refresh,
            restart_requests=list(restart_requests or []),
        )

    monkeypatch.setattr(vscode_manage, "RunserverSession", _factory)


def test_runserver_runs_env_refresh_when_requested(monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(vscode_manage, "_run_env_refresh", lambda base_dir: calls.append(base_dir))
    _patch_session(monkeypatch, should_run_env_refresh=True)
    monkeypatch.setattr(runpy, "run_path", lambda *args, **kwargs: None)

    vscode_manage.main(["runserver"])

    assert calls


def test_runserver_skips_env_refresh_when_migration_server_active(monkeypatch):
    monkeypatch.setattr(vscode_manage, "_run_env_refresh", lambda base_dir: (_ for _ in ()).throw(AssertionError))
    _patch_session(monkeypatch, should_run_env_refresh=False)
    monkeypatch.setattr(runpy, "run_path", lambda *args, **kwargs: None)

    vscode_manage.main(["runserver"])


def test_runserver_restarts_on_request(monkeypatch):
    _patch_session(monkeypatch, should_run_env_refresh=False, restart_requests=[True])
    call_count = 0

    def fake_run_path(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise KeyboardInterrupt
        return None

    monkeypatch.setattr(runpy, "run_path", fake_run_path)
    monkeypatch.setattr(vscode_manage, "_run_env_refresh", lambda base_dir: None)

    vscode_manage.main(["runserver"])

    assert call_count == 2


def test_runserver_propagates_keyboard_interrupt(monkeypatch):
    _patch_session(monkeypatch, should_run_env_refresh=False, restart_requests=[])

    monkeypatch.setattr(vscode_manage, "_run_env_refresh", lambda base_dir: None)
    monkeypatch.setattr(runpy, "run_path", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        vscode_manage.main(["runserver"])


def test_runserver_session_records_state_and_restart(tmp_path: Path, monkeypatch):
    lock_dir = tmp_path / "locks"
    monkeypatch.setattr(vscode_manage, "_is_migration_server_running", lambda *_: False)
    requests: list[None] = []

    session = vscode_manage.RunserverSession(
        tmp_path,
        ["runserver"],
        False,
        poll_interval=0.01,
        interrupt_main=lambda: requests.append(None),
    )

    with session:
        assert session.should_run_env_refresh is True
        assert session.state_path.exists()
        time.sleep(0.05)
        session.restart_path.write_text(json.dumps({"when": time.time()}))
        for _ in range(50):
            if requests:
                break
            time.sleep(0.01)
        assert requests
        assert session.consume_restart_request() is True

    assert not session.state_path.exists()
    assert not any(lock_dir.glob("vscode_runserver.restart.*"))
