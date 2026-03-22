"""Regression tests for migration server process detection helpers."""

from __future__ import annotations

from pathlib import Path

from apps.core.tasks.migrations import _is_migration_server_process


def test_is_migration_server_process_accepts_module_entrypoint() -> None:
    base_dir = Path("/workspace/arthexis")

    assert _is_migration_server_process(
        [
            str(base_dir / ".venv/bin/python"),
            "-m",
            "utils.devtools.migration_server",
            "--server",
        ],
        base_dir,
    )


def test_is_migration_server_process_accepts_legacy_wrapper_path() -> None:
    base_dir = Path("/workspace/arthexis")

    assert _is_migration_server_process(
        [
            str(base_dir / ".venv/bin/python"),
            str(base_dir / "scripts/migration_server.py"),
        ],
        base_dir,
    )


def test_is_migration_server_process_accepts_relative_legacy_wrapper_path() -> None:
    base_dir = Path("/workspace/arthexis")

    assert _is_migration_server_process(
        [str(base_dir / ".venv/bin/python"), "./scripts/migration_server.py"],
        base_dir,
    )


def test_is_migration_server_process_rejects_non_module_argument_match() -> None:
    base_dir = Path("/workspace/arthexis")

    assert not _is_migration_server_process(
        [
            str(base_dir / ".venv/bin/python"),
            str(base_dir / "some_script.py"),
            "--module=utils.devtools.migration_server",
        ],
        base_dir,
    )


def test_is_migration_server_process_rejects_other_commands() -> None:
    base_dir = Path("/workspace/arthexis")

    assert not _is_migration_server_process(
        [str(base_dir / ".venv/bin/python"), str(base_dir / "manage.py"), "runserver"],
        base_dir,
    )
