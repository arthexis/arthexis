from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts import test_server


def test_build_pytest_command_respects_last_failed() -> None:
    base_command = test_server.build_pytest_command(use_last_failed=False)
    with_last_failed = test_server.build_pytest_command(use_last_failed=True)

    assert base_command == [test_server.sys.executable, "-m", "pytest"]
    assert with_last_failed[-1] == "--last-failed"


def test_run_tests_uses_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], cwd: Path, env: dict[str, str]):
        calls.append({"command": command, "cwd": cwd, "env": env.copy()})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_server.subprocess, "run", fake_run)

    result = test_server.run_tests(tmp_path, use_last_failed=False)

    assert result is True
    assert calls[0]["command"] == [test_server.sys.executable, "-m", "pytest"]
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["env"].get("DJANGO_SETTINGS_MODULE") == "config.settings"


def test_run_tests_notifies_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    notifications: list[tuple[str, str]] = []

    monkeypatch.setattr(
        test_server,
        "notify_async",
        lambda subject, body="": notifications.append((subject, body)),
    )
    monkeypatch.setattr(
        test_server.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    result = test_server.run_tests(tmp_path, use_last_failed=False)

    assert result is True
    assert notifications == [("Test server run completed", "Pytest passed.")]


def test_run_tests_notifies_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    notifications: list[tuple[str, str]] = []

    monkeypatch.setattr(
        test_server,
        "notify_async",
        lambda subject, body="": notifications.append((subject, body)),
    )
    monkeypatch.setattr(
        test_server.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    result = test_server.run_tests(tmp_path, use_last_failed=False)

    assert result is False
    assert notifications == [
        ("Test server run completed", "Pytest failed. Check VS Code output."),
    ]


def test_run_tests_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    notifications: list[tuple[str, str]] = []

    monkeypatch.setattr(
        test_server,
        "notify_async",
        lambda subject, body="": notifications.append((subject, body)),
    )
    monkeypatch.setattr(
        test_server.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    result = test_server.run_tests(tmp_path, use_last_failed=False)

    assert result is False
    assert notifications == [
        ("Test server run completed", "Pytest failed. Check VS Code output."),
    ]


def test_run_migrations_delegates_to_env_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_env_refresh(base_dir: Path, *, latest: bool) -> bool:
        calls.append({"base_dir": base_dir, "latest": latest})
        return True

    monkeypatch.setattr(test_server.migration_server, "run_env_refresh", fake_run_env_refresh)

    result = test_server.run_migrations(tmp_path, latest=False)

    assert result is True
    assert calls == [{"base_dir": tmp_path, "latest": False}]


def test_main_runs_until_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sequence: list[str] = []

    def fake_collect(_: Path):
        sequence.append("collect")
        return {}

    def fake_wait_for_changes(_: Path, __: dict[str, int], **___: object):
        sequence.append("wait")
        raise KeyboardInterrupt

    def fake_run_migrations(_: Path, *, latest: bool) -> bool:
        sequence.append(f"migrate:{latest}")
        return True

    def fake_run_tests(_: Path, *, use_last_failed: bool) -> bool:
        sequence.append(f"run:{use_last_failed}")
        return False

    monkeypatch.setattr(test_server, "BASE_DIR", tmp_path)
    monkeypatch.setattr(test_server.migration_server, "collect_source_mtimes", fake_collect)
    monkeypatch.setattr(test_server.migration_server, "wait_for_changes", fake_wait_for_changes)
    monkeypatch.setattr(test_server, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(test_server, "run_tests", fake_run_tests)

    result = test_server.main([])

    assert result == 0
    assert sequence == ["collect", "migrate:True", "run:False", "wait"]


def test_main_skips_tests_when_migrations_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sequence: list[str] = []

    def fake_collect(_: Path):
        sequence.append("collect")
        return {}

    def fake_wait_for_changes(_: Path, __: dict[str, int], **___: object):
        sequence.append("wait")
        raise KeyboardInterrupt

    def fake_run_migrations(_: Path, *, latest: bool) -> bool:
        sequence.append("migrate")
        return False

    def fake_run_tests(_: Path, *, use_last_failed: bool) -> bool:
        sequence.append("run")
        return True

    monkeypatch.setattr(test_server, "BASE_DIR", tmp_path)
    monkeypatch.setattr(test_server.migration_server, "collect_source_mtimes", fake_collect)
    monkeypatch.setattr(test_server.migration_server, "wait_for_changes", fake_wait_for_changes)
    monkeypatch.setattr(test_server, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(test_server, "run_tests", fake_run_tests)

    result = test_server.main([])

    assert result == 0
    assert sequence == ["collect", "migrate", "wait"]
