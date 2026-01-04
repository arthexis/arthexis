from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER_PATH = REPO_ROOT / "scripts" / "helpers" / "runserver_preflight.sh"


def _write_manage_stub(base_dir: Path) -> None:
    manage_py = base_dir / "manage.py"
    manage_py.write_text(
        """#!/usr/bin/env python
import os
import pathlib
import sys

log_path = pathlib.Path(os.environ['COMMAND_LOG'])
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open('a', encoding='utf-8') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')

plan_output = os.environ.get('SHOWMIGRATIONS_PLAN', '[ ] demo 0001_initial')
if sys.argv[1] == 'showmigrations':
    print(plan_output)
elif sys.argv[1] == 'migrate':
    exit_code = 0
    if '--check' in sys.argv:
        status = os.environ.get('MIGRATE_CHECK_STATUS', '0')
        status_file = os.environ.get('MIGRATE_CHECK_STATUS_FILE')
        if status_file:
            path = pathlib.Path(status_file)
            if path.exists():
                statuses = path.read_text().splitlines()
                if statuses:
                    status = statuses[0]
                    path.write_text('\\n'.join(statuses[1:]))
        exit_code = int(status)
    else:
        exit_code = int(os.environ.get('MIGRATE_STATUS', '0'))
    sys.exit(exit_code)
sys.exit(0)
"""
    )
    manage_py.chmod(0o755)


def _write_migration(base_dir: Path, contents: str = "# initial migration\n") -> Path:
    migration_dir = base_dir / "apps" / "demo" / "migrations"
    migration_dir.mkdir(parents=True, exist_ok=True)
    migration_file = migration_dir / "0001_initial.py"
    migration_file.write_text(contents)
    return migration_file


def _run_preflight(
    base_dir: Path,
    plan_output: str = "[ ] demo 0001_initial",
    force_refresh: bool = False,
    migrate_check_status: str = "0",
    migrate_check_sequence: list[str] | None = None,
):
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    command_log = base_dir / "command.log"
    if command_log.exists():
        command_log.unlink()

    extra_args_log = base_dir / "extra_args.log"
    if extra_args_log.exists():
        extra_args_log.unlink()

    env = os.environ.copy()

    status_file = None
    if migrate_check_sequence:
        status_file = base_dir / "migrate_check_statuses.txt"
        status_file.write_text("\n".join(migrate_check_sequence))

    env.update(
        {
            "BASE_DIR": str(base_dir),
            "LOCK_DIR": str(lock_dir),
            "COMMAND_LOG": str(command_log),
            "SHOWMIGRATIONS_PLAN": plan_output,
            "MIGRATE_CHECK_STATUS": migrate_check_status,
            "MIGRATE_CHECK_STATUS_FILE": str(status_file) if status_file else "",
            "RUNSERVER_PREFLIGHT_FORCE_REFRESH": "true" if force_refresh else "false",
        }
    )

    subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -e",
                    f"cd '{base_dir}'",
                    "RUNSERVER_EXTRA_ARGS=()",
                    f"source '{HELPER_PATH}'",
                    "run_runserver_preflight",
                    f"printf '%s\\n' \"${{RUNSERVER_EXTRA_ARGS[@]}}\" > '{extra_args_log}'",
                ]
            ),
        ],
        check=True,
        env=env,
    )

    fingerprint_path = lock_dir / "migrations.sha"
    fingerprint = fingerprint_path.read_text().strip()

    commands = command_log.read_text().splitlines() if command_log.exists() else []
    extra_args = extra_args_log.read_text().splitlines() if extra_args_log.exists() else []

    return {
        "fingerprint": fingerprint,
        "commands": commands,
        "extra_args": extra_args,
    }


def test_runserver_preflight_reuses_cached_fingerprint(tmp_path: Path):
    _write_manage_stub(tmp_path)
    _write_migration(tmp_path)

    first_run = _run_preflight(tmp_path, plan_output="[X] demo 0001_initial")
    assert "showmigrations --plan" in first_run["commands"][0]
    assert first_run["extra_args"] == ["--skip-checks"]

    second_run = _run_preflight(tmp_path, plan_output="[X] demo 0001_initial")
    assert second_run["commands"] == ["migrate --check"]
    assert second_run["extra_args"] == ["--skip-checks"]
    assert second_run["fingerprint"] == first_run["fingerprint"]


def test_runserver_preflight_invalidates_on_migration_change(tmp_path: Path):
    _write_manage_stub(tmp_path)
    migration_file = _write_migration(tmp_path)

    initial_run = _run_preflight(tmp_path, plan_output="[X] demo 0001_initial")

    migration_file.write_text("# updated migration\n")

    refreshed_run = _run_preflight(tmp_path, plan_output="[ ] demo 0001_initial")
    assert refreshed_run["fingerprint"] != initial_run["fingerprint"]
    assert "showmigrations --plan" in refreshed_run["commands"][0]
    assert "migrate --noinput" in refreshed_run["commands"][1]
    assert "migrate --check" in refreshed_run["commands"][2]
    assert refreshed_run["extra_args"] == ["--skip-checks"]


def test_runserver_preflight_rebuilds_when_db_missing(tmp_path: Path):
    _write_manage_stub(tmp_path)
    _write_migration(tmp_path)

    successful_run = _run_preflight(tmp_path, plan_output="[X] demo 0001_initial")
    assert any(cmd == "migrate --check" for cmd in successful_run["commands"])

    wiped_db_run = _run_preflight(
        tmp_path,
        plan_output="[ ] demo 0001_initial",
        migrate_check_sequence=["1", "0"],
    )

    assert wiped_db_run["commands"][0] == "migrate --check"
    assert "showmigrations --plan" in wiped_db_run["commands"][1]
    assert "migrate --noinput" in wiped_db_run["commands"][2]
    assert "migrate --check" in wiped_db_run["commands"][3]
    assert wiped_db_run["fingerprint"] == successful_run["fingerprint"]
