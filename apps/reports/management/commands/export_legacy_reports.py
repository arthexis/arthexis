from __future__ import annotations

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.reports.models import SQLReport


LEGACY_REPORT_TYPE = "legacy_archived"
LEGACY_COLUMN = "legacy_definition"
REPORT_TABLE = SQLReport._meta.db_table


class Command(BaseCommand):
    """Export archived report definitions before removing their legacy schema."""

    help = (
        "Export legacy_archived SQL reports to JSON. Pass --delete to remove "
        "the exported rows so the legacy-schema migration can proceed."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("output", help="Destination JSON file.")
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete archived rows after the export is durably written.",
        )

    def handle(self, *args, **options) -> None:
        output_path = Path(options["output"]).expanduser()
        columns = self._table_columns()
        if LEGACY_COLUMN not in columns:
            raise CommandError(
                "The legacy report definition column is already absent; "
                "there is nothing left to export."
            )

        records = self._load_legacy_records()
        payload = {
            "format": "arthexis-legacy-reports-v1",
            "report_type": LEGACY_REPORT_TYPE,
            "count": len(records),
            "reports": records,
        }
        self._atomic_write_json(output_path, payload)

        if options["delete"] and records:
            ids = [record["id"] for record in records]
            with transaction.atomic():
                SQLReport.objects.filter(pk__in=ids, report_type=LEGACY_REPORT_TYPE).delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Exported and deleted {len(ids)} archived report(s): {output_path}"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {len(records)} archived report(s): {output_path}"
            )
        )
        if records and not options["delete"]:
            self.stdout.write(
                "Archived rows remain in the database; rerun with --delete before migrating."
            )

    def _table_columns(self) -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(
                cursor, REPORT_TABLE
            )
        return {column.name for column in description}

    def _load_legacy_records(self) -> list[dict[str, object]]:
        quoted_table = connection.ops.quote_name(REPORT_TABLE)
        selected_columns = (
            "id",
            "name",
            "parameters",
            "database_alias",
            "query",
            "html_template_name",
            LEGACY_COLUMN,
            "schedule_enabled",
            "schedule_interval_minutes",
            "next_scheduled_run_at",
            "last_run_at",
            "created_at",
            "updated_at",
        )
        quoted_columns = ", ".join(
            connection.ops.quote_name(column) for column in selected_columns
        )
        report_type_column = connection.ops.quote_name("report_type")
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {quoted_columns} FROM {quoted_table} "
                f"WHERE {report_type_column} = %s ORDER BY id",
                [LEGACY_REPORT_TYPE],
            )
            rows = cursor.fetchall()

        records: list[dict[str, object]] = []
        for row in rows:
            record = dict(zip(selected_columns, row, strict=True))
            for field in ("parameters", LEGACY_COLUMN):
                value = record[field]
                if isinstance(value, str):
                    try:
                        record[field] = json.loads(value)
                    except json.JSONDecodeError:
                        pass
            records.append(record)
        return records

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
