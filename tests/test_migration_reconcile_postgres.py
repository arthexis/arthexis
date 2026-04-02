from __future__ import annotations

from pathlib import Path

from scripts.helpers.migration_reconcile_postgres import (
    backup_postgres_database,
    drop_postgres_temp_database,
    restore_postgres_snapshot_to_temp_database,
)


def test_backup_postgres_database_invokes_pg_dump(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def _fake_run(command, check, env):  # type: ignore[no-untyped-def]
        assert check is True
        calls.append((command, env))

    monkeypatch.setattr("subprocess.run", _fake_run)

    default_db = {
        "NAME": "arthexis",
        "HOST": "db.example",
        "PORT": "5432",
        "USER": "arthexis",
        "PASSWORD": "secret",
    }

    backup_path = backup_postgres_database(default_db, tmp_path)

    assert backup_path == tmp_path / "arthexis.pre_major_migrate.dump"
    assert calls
    command, env = calls[0]
    assert command[:4] == ["pg_dump", "--format=custom", "--file", str(backup_path)]
    assert "--host" in command
    assert "--username" in command
    assert env["PGPASSWORD"] == "secret"


def test_restore_and_drop_temp_database_issue_psql_and_pg_restore(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(command, check, env):  # type: ignore[no-untyped-def]
        assert check is True
        calls.append(command)

    monkeypatch.setattr("subprocess.run", _fake_run)

    snapshot = tmp_path / "arthexis.pre_major_migrate.dump"
    snapshot.write_text("placeholder")
    default_db = {"NAME": "arthexis", "USER": "postgres"}

    restore_postgres_snapshot_to_temp_database(
        snapshot_path=snapshot,
        default_db=default_db,
        temp_db_name="arthexis_pre_major_migrate_snapshot",
    )
    drop_postgres_temp_database(
        default_db=default_db,
        temp_db_name="arthexis_pre_major_migrate_snapshot",
    )

    assert len(calls) == 3
    assert calls[0][0] == "psql"
    assert calls[1][0] == "pg_restore"
    assert calls[2][0] == "psql"


def test_restore_temp_database_rejects_invalid_db_name(tmp_path: Path) -> None:
    snapshot = tmp_path / "arthexis.pre_major_migrate.dump"
    snapshot.write_text("placeholder")

    try:
        restore_postgres_snapshot_to_temp_database(
            snapshot_path=snapshot,
            default_db={"NAME": "arthexis"},
            temp_db_name="bad name;drop database",
        )
    except ValueError as exc:
        assert "Invalid database name" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid database name")
