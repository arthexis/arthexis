from __future__ import annotations

import errno
import subprocess
from pathlib import Path

import pytest

import manage
from gate_markers import gate


pytestmark = [gate.upgrade, pytest.mark.django_db]


def _configure_manage_main(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(manage, "__file__", str(tmp_path / "manage.py"))
    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(manage, "_run_runserver", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage, "_execute_django", lambda *_args, **_kwargs: None)


def _env_refresh_command(tmp_path: Path) -> list[str]:
    return [
        manage.sys.executable,
        str(tmp_path / "env-refresh.py"),
        "--latest",
        "database",
    ]


def _assert_env_refresh_context(captured_err: str, *, command_str: str) -> None:
    assert "Environment refresh failed before runserver startup." in captured_err
    assert f"Failed command: {command_str}" in captured_err
    assert "Re-run manually for full details:" in captured_err
    assert f"{command_str} --reconcile" in captured_err


def _capture_popen_calls(monkeypatch, *, return_process=None) -> list[list[str]]:
    popen_calls: list[list[str]] = []
    monkeypatch.setattr(
        manage.subprocess,
        "Popen",
        lambda command: popen_calls.append(command) or return_process,
    )
    return popen_calls


def _assert_exit_code(exc: SystemExit, *, expected: int) -> None:
    assert exc.code == expected


def _write_service_mode_lock(tmp_path: Path, value: str) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(exist_ok=True)
    (lock_dir / "service_mode.lck").write_text(value, encoding="utf-8")


def _write_celery_lock(tmp_path: Path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(exist_ok=True)
    (lock_dir / "celery.lck").write_text("", encoding="utf-8")


def _write_role_lock(tmp_path: Path, value: str) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(exist_ok=True)
    (lock_dir / "role.lck").write_text(value, encoding="utf-8")


def test_service_mode_disables_embedded_celery_in_systemd(tmp_path: Path) -> None:
    _write_service_mode_lock(tmp_path, "systemd\n")

    assert not manage._service_mode_allows_embedded_celery(tmp_path)


def test_service_mode_allows_embedded_celery_on_os_error(
    monkeypatch, tmp_path: Path
) -> None:
    _write_service_mode_lock(tmp_path, "systemd\n")

    def raise_permission_error(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "read_text", raise_permission_error)

    assert manage._service_mode_allows_embedded_celery(tmp_path)


def test_main_skips_embedded_celery_for_systemd_mode(
    monkeypatch, tmp_path: Path
) -> None:
    _write_celery_lock(tmp_path)
    _write_service_mode_lock(tmp_path, "systemd")

    _configure_manage_main(monkeypatch, tmp_path)
    popen_calls = _capture_popen_calls(monkeypatch)

    manage.main(["runserver"])

    assert popen_calls == []


def test_main_skips_embedded_celery_by_default_for_terminal_role(
    monkeypatch, tmp_path: Path
) -> None:
    _write_celery_lock(tmp_path)
    _write_role_lock(tmp_path, "Terminal\n")

    _configure_manage_main(monkeypatch, tmp_path)
    popen_calls = _capture_popen_calls(monkeypatch)

    manage.main(["runserver"])

    assert popen_calls == []


def test_main_allows_embedded_celery_for_non_terminal_role(
    monkeypatch, tmp_path: Path
) -> None:
    _write_celery_lock(tmp_path)
    _write_role_lock(tmp_path, "Control\n")

    class DummyProc:
        def terminate(self) -> None:
            return

    _configure_manage_main(monkeypatch, tmp_path)
    popen_calls = _capture_popen_calls(monkeypatch, return_process=DummyProc())

    manage.main(["runserver"])

    assert len(popen_calls) == 2




def test_main_skips_embedded_celery_when_node_role_env_is_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    _write_celery_lock(tmp_path)
    monkeypatch.setenv("NODE_ROLE", "Terminal")

    _configure_manage_main(monkeypatch, tmp_path)
    popen_calls = _capture_popen_calls(monkeypatch)

    manage.main(["runserver"])

    assert popen_calls == []


def test_main_allows_embedded_celery_when_node_role_env_is_non_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    _write_celery_lock(tmp_path)
    monkeypatch.setenv("NODE_ROLE", "Control")

    class DummyProc:
        def terminate(self) -> None:
            return

    _configure_manage_main(monkeypatch, tmp_path)
    popen_calls = _capture_popen_calls(monkeypatch, return_process=DummyProc())

    manage.main(["runserver"])

    assert len(popen_calls) == 2


def test_is_terminal_node_defaults_to_terminal_on_role_lock_decode_error(
    monkeypatch, tmp_path: Path
) -> None:
    _write_role_lock(tmp_path, "Terminal\n")

    role_lock = tmp_path / ".locks" / "role.lck"
    original_read_text = Path.read_text

    def raise_decode_error(self, *args, **kwargs):
        if self == role_lock:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raise_decode_error)

    assert manage._is_terminal_node(tmp_path)

def test_main_preserves_environment_debug_for_runserver(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def capture_runserver(
        _base_dir: Path, argv: list[str], is_debug_session: bool
    ) -> None:
        captured["argv"] = argv
        captured["is_debug_session"] = is_debug_session

    monkeypatch.setenv("DEBUG", "1")
    _configure_manage_main(monkeypatch, tmp_path)
    monkeypatch.setattr(manage, "_run_runserver", capture_runserver)

    manage.main(["runserver"])

    assert manage.os.environ["DEBUG"] == "1"
    assert captured["is_debug_session"] is False


def test_main_allows_explicit_embedded_celery_override(
    monkeypatch, tmp_path: Path
) -> None:
    _write_service_mode_lock(tmp_path, "systemd")

    class DummyProc:
        def terminate(self) -> None:
            return

    _configure_manage_main(monkeypatch, tmp_path)
    popen_calls = _capture_popen_calls(monkeypatch, return_process=DummyProc())

    manage.main(["runserver", "--celery"])

    assert len(popen_calls) == 2


def test_main_does_not_check_service_mode_outside_runserver(
    monkeypatch, tmp_path: Path
) -> None:
    _configure_manage_main(monkeypatch, tmp_path)
    monkeypatch.setattr(
        manage,
        "_service_mode_allows_embedded_celery",
        lambda _base_dir: (_ for _ in ()).throw(AssertionError("unexpected call")),
    )

    manage.main(["check"])


def test_run_env_refresh_runs_latest_database_refresh(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(manage.subprocess, "run", fake_run)

    manage._run_env_refresh(tmp_path)

    assert captured["command"] == _env_refresh_command(tmp_path)
    kwargs = captured["kwargs"]
    assert kwargs["check"] is True
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["DJANGO_SETTINGS_MODULE"] == "config.settings"


@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(
            lambda command: subprocess.CalledProcessError(1, command),
            id="called-process-error",
        ),
    ],
)
def test_run_env_refresh_exits_with_context_when_refresh_fails(
    monkeypatch, tmp_path: Path, capsys, error_factory
) -> None:
    command = _env_refresh_command(tmp_path)
    command_str = manage.shlex.join(command)

    def fake_run(*_args, **_kwargs):
        raise error_factory(command)

    monkeypatch.setattr(manage.subprocess, "run", fake_run)

    try:
        manage._run_env_refresh(tmp_path)
    except SystemExit as exc:
        _assert_exit_code(exc, expected=1)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected SystemExit")

    captured = capsys.readouterr()
    _assert_env_refresh_context(captured.err, command_str=command_str)
