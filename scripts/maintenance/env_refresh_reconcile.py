from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import CommandError

from scripts.helpers.migration_reconcile import backup_sqlite_database
from scripts.helpers.migration_reconcile_common import ReconcileReport
from scripts.helpers.migration_reconcile_postgres import backup_postgres_database

SQLITE_MIGRATION_RECOVERY_MESSAGE = (
    "Detected migration graph/version mismatch for this database.\n"
    "Re-run the upgrade workflow with reconciliation enabled:\n"
    "  ./upgrade.sh --migrate"
)

NON_SQLITE_MIGRATION_RECOVERY_MESSAGE = (
    "Detected migration graph/version mismatch for this database.\n"
    "Re-run the standard upgrade workflow and apply migrations manually for "
    "your database backend."
)

POSTGRES_RECONCILE_TEMP_DB = "arthexis_pre_major_migrate_snapshot"


def migration_recovery_message(using_sqlite: bool) -> str:
    """Return operator guidance for migration graph/version mismatch failures."""

    if using_sqlite:
        return SQLITE_MIGRATION_RECOVERY_MESSAGE
    return NON_SQLITE_MIGRATION_RECOVERY_MESSAGE


def emit_reconciliation_report(report: ReconcileReport) -> None:
    """Print a backend-agnostic reconciliation summary."""

    skipped_total = len(report.skipped_tables) + sum(report.skipped_rows.values())
    missing_total = len(report.missing_in_target) + len(report.missing_in_source)
    print(
        "Reconciliation summary: "
        f"copied={len(report.copied_tables)} "
        f"skipped={skipped_total} "
        f"missing={missing_total}",
        flush=True,
    )
    print(
        "Major-version migration reconciliation "
        f"({report.backend}) copied {len(report.copied_tables)} table(s).",
        flush=True,
    )
    if report.missing_in_target:
        print(
            "Ignored legacy-only tables (not in current schema): "
            f"{', '.join(report.missing_in_target)}",
            flush=True,
        )
    if report.missing_in_source:
        print(
            "Ignored new-schema tables missing from legacy database: "
            f"{', '.join(report.missing_in_source)}",
            flush=True,
        )
    if report.skipped_columns:
        print("Skipped columns not present in legacy schema:", flush=True)
        for table, columns in sorted(report.skipped_columns.items()):
            print(f"  - {table}: {', '.join(columns)}", flush=True)
    if report.skipped_rows:
        print("Rows skipped during conflict-tolerant insert:", flush=True)
        for table, count in sorted(report.skipped_rows.items()):
            print(f"  - {table}: {count}", flush=True)
    if report.skipped_tables:
        print("Skipped incompatible tables:", flush=True)
        for table, reason in sorted(report.skipped_tables.items()):
            print(f"  - {table}: {reason}", flush=True)


def prepare_reconcile_snapshot(
    *,
    using_sqlite: bool,
    default_db: dict[str, Any],
    locks_dir: Path,
    base_dir: Path,
) -> tuple[Path | None, Path | None, str | None]:
    """Capture a pre-migration reconciliation snapshot when possible."""

    if using_sqlite:
        reconcile_db_path = Path(default_db["NAME"])
        reconcile_backup_db = (
            locks_dir
            / f"{reconcile_db_path.stem}.pre_major_migrate{reconcile_db_path.suffix}"
        )
        if reconcile_backup_db.exists():
            print(
                "Reusing preserved pre-migration backup for major-version "
                f"reconciliation: {reconcile_backup_db.relative_to(base_dir)}",
                flush=True,
            )
            return reconcile_backup_db, reconcile_db_path, None
        if not reconcile_db_path.exists():
            return None, reconcile_db_path, None
        reconcile_backup_db = backup_sqlite_database(
            reconcile_db_path,
            locks_dir,
        )
        print(
            "Prepared pre-migration backup for major-version reconciliation: "
            f"{reconcile_backup_db.relative_to(base_dir)}",
            flush=True,
        )
        return reconcile_backup_db, reconcile_db_path, None
    if default_db["ENGINE"] == "django.db.backends.postgresql":
        reconcile_backup_db = backup_postgres_database(default_db, locks_dir)
        print(
            "Prepared PostgreSQL snapshot for major-version reconciliation: "
            f"{reconcile_backup_db.relative_to(base_dir)}",
            flush=True,
        )
        return reconcile_backup_db, None, POSTGRES_RECONCILE_TEMP_DB
    raise CommandError("--migrate supports only SQLite and PostgreSQL backends.")
