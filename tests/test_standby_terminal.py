from __future__ import annotations

from pathlib import Path

from scripts import standby_terminal


def test_resolve_paths_keeps_checkout_and_runtime_isolated(tmp_path: Path) -> None:
    paths = standby_terminal.resolve_paths(
        source_checkout=tmp_path / "source",
        state_dir=tmp_path / "state",
    )

    assert paths.checkout == tmp_path / "state" / "checkout"
    assert paths.db_path == tmp_path / "state" / "runtime" / "db.sqlite3"
    assert paths.test_db_path == tmp_path / "state" / "runtime" / "test_db.sqlite3"
    assert paths.log_dir == tmp_path / "state" / "logs"
    assert paths.pid_file == tmp_path / "state" / "standby-terminal.pid"


def test_runtime_env_uses_terminal_role_and_isolated_state(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", "main.sqlite3")
    paths = standby_terminal.resolve_paths(
        source_checkout=tmp_path / "source",
        state_dir=tmp_path / "state",
    )

    env = standby_terminal.runtime_env(paths, port=8000)

    assert env["ARTHEXIS_DB_BACKEND"] == "sqlite"
    assert env["ARTHEXIS_SQLITE_PATH"] == str(paths.db_path)
    assert env["ARTHEXIS_SQLITE_TEST_PATH"] == str(paths.test_db_path)
    assert env["ARTHEXIS_LOG_DIR"] == str(paths.log_dir)
    assert env["DJANGO_CACHE_DIR"] == str(paths.cache_dir)
    assert env["NODE_ROLE"] == "Terminal"
    assert env["NET_MESSAGE_DISABLE_PROPAGATION"] == "1"
    assert env["PORT"] == "8000"


def test_start_command_uses_platform_entrypoint(monkeypatch, tmp_path: Path) -> None:
    paths = standby_terminal.resolve_paths(
        source_checkout=tmp_path / "source",
        state_dir=tmp_path / "state",
    )
    monkeypatch.setattr(standby_terminal.os, "name", "nt")

    command = standby_terminal.start_command(paths, port=8000)

    assert command == [str(paths.checkout / "start.bat"), "--port", "8000"]


def test_write_runtime_locks_records_terminal_role_and_port(tmp_path: Path) -> None:
    paths = standby_terminal.resolve_paths(
        source_checkout=tmp_path / "source",
        state_dir=tmp_path / "state",
    )
    paths.checkout.mkdir(parents=True)

    standby_terminal.write_runtime_locks(paths, port=8000)

    assert (paths.checkout / ".locks" / "role.lck").read_text(
        encoding="utf-8"
    ) == "Terminal\n"
    assert (paths.checkout / ".locks" / "backend_port.lck").read_text(
        encoding="utf-8"
    ) == "8000\n"
    assert str(paths.state_dir) in (
        paths.checkout / ".locks" / "standby-terminal.lck"
    ).read_text(encoding="utf-8")


def test_validation_report_records_commands(tmp_path: Path) -> None:
    paths = standby_terminal.resolve_paths(
        source_checkout=tmp_path / "source",
        state_dir=tmp_path / "state",
    )
    result = standby_terminal.CommandResult(
        command=["python", "manage.py", "check"],
        returncode=0,
        stdout="ok",
        stderr="",
    )

    report = standby_terminal.write_validation_report(
        paths,
        [result],
        source_db=None,
    )

    report_text = report.read_text(encoding="utf-8")
    assert "Standby Terminal Cutover Validation" in report_text
    assert "`python manage.py check`" in report_text
    assert "- Exit code: `0`" in report_text
