from __future__ import annotations

import errno
from pathlib import Path

import manage


def test_service_mode_allows_embedded_celery_by_default(tmp_path: Path) -> None:
    assert manage._service_mode_allows_embedded_celery(tmp_path)


def test_service_mode_disables_embedded_celery_in_systemd(tmp_path: Path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "service_mode.lck").write_text("systemd\n", encoding="utf-8")

    assert not manage._service_mode_allows_embedded_celery(tmp_path)


def test_service_mode_allows_embedded_celery_on_os_error(
    monkeypatch, tmp_path: Path
) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    lock_file = lock_dir / "service_mode.lck"
    lock_file.write_text("systemd\n", encoding="utf-8")

    def raise_permission_error(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "read_text", raise_permission_error)

    assert manage._service_mode_allows_embedded_celery(tmp_path)


def test_main_skips_embedded_celery_for_systemd_mode(
    monkeypatch, tmp_path: Path
) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "celery.lck").write_text("", encoding="utf-8")
    (lock_dir / "service_mode.lck").write_text("systemd", encoding="utf-8")

    popen_calls: list[list[str]] = []
    monkeypatch.setattr(manage, "__file__", str(tmp_path / "manage.py"))
    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(manage, "_run_runserver", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage, "_execute_django", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        manage.subprocess,
        "Popen",
        lambda command: popen_calls.append(command),
    )

    manage.main(["runserver"])

    assert popen_calls == []


def test_main_allows_explicit_embedded_celery_override(
    monkeypatch, tmp_path: Path
) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "service_mode.lck").write_text("systemd", encoding="utf-8")

    popen_calls: list[list[str]] = []

    class DummyProc:
        def terminate(self) -> None:
            return

    monkeypatch.setattr(manage, "__file__", str(tmp_path / "manage.py"))
    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(manage, "_run_runserver", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage, "_execute_django", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        manage.subprocess,
        "Popen",
        lambda command: popen_calls.append(command) or DummyProc(),
    )

    manage.main(["runserver", "--celery"])

    assert len(popen_calls) == 2


def test_main_does_not_check_service_mode_outside_runserver(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(manage, "__file__", str(tmp_path / "manage.py"))
    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(manage, "_run_runserver", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage, "_execute_django", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        manage,
        "_service_mode_allows_embedded_celery",
        lambda _base_dir: (_ for _ in ()).throw(AssertionError("unexpected call")),
    )

    manage.main(["check"])
