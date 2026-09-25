#!/usr/bin/env python3
"""Capture, restore, and reconcile legacy Arthexis data from Arthexis 2.0."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from arthexis.reconciliation.source import inspect_source, resolve_source


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "capture",
            "verify",
            "restore",
            "reconcile-fixture",
            "inspect",
            "dry-run",
            "import",
        ),
        help="capture/restore a legacy source or run reconciliation",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="legacy installation/database, capture bundle, or migration fixture",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="explicit legacy SQLite database path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="capture/fixture destination root (defaults under ARTHEXIS_DATA_DIR)",
    )
    parser.add_argument(
        "--destination-database",
        type=Path,
        help=(
            "fresh Arthexis 2 database produced by reconcile-fixture "
            "(defaults to <fixture>/reconciled.sqlite3)"
        ),
    )
    return parser


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")
    import django

    django.setup()


def _require_source(source: Path | None) -> Path:
    if source is None:
        raise SystemExit("Provide a source path.")
    return source


def _reconcile_fixture(arguments: argparse.Namespace) -> int:
    source = _require_source(arguments.source).expanduser().resolve()
    from arthexis.reconciliation.workspace import (
        reconcile_fixture,
        verify_fixture_source,
        write_failure_receipt,
    )

    verify_fixture_source(source)
    destination = (
        arguments.destination_database.expanduser().resolve()
        if arguments.destination_database
        else source / "reconciled.sqlite3"
    )
    if destination.exists():
        raise SystemExit(f"Destination database already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    os.environ["ARTHEXIS_DATABASE_PATH"] = str(destination)
    try:
        _setup_django()
        from django.core.management import call_command

        call_command("migrate", interactive=False, verbosity=0)
        call_command("seed", verbosity=0)
        report, receipt = reconcile_fixture(source, destination)
    except Exception as error:
        diagnostic = write_failure_receipt(source, error)
        if diagnostic is not None:
            print(f"Reconciliation diagnostic: {diagnostic}", file=sys.stderr)
        raise

    print(
        json.dumps(
            {
                "fixture": str(source),
                "destination_database": str(destination),
                "receipt": str(receipt),
                "reconciliation": report.as_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    arguments = _parser().parse_args()

    if arguments.command == "reconcile-fixture":
        return _reconcile_fixture(arguments)

    if arguments.command in {"capture", "verify", "restore"}:
        from arthexis.reconciliation.capture import (
            capture_legacy_installation,
            verify_capture,
        )
        from arthexis.reconciliation.fixture import restore_fixture

        source = _require_source(arguments.source)
        if arguments.command == "verify":
            result = verify_capture(source)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        _setup_django()
        from django.conf import settings

        if arguments.command == "restore":
            destination = (
                arguments.output
                or Path(settings.DATA_DIR) / "migration" / "fixtures"
            )
            result = restore_fixture(source, destination)
            print(
                json.dumps(
                    {
                        "fixture_id": result.fixture_id,
                        "path": str(result.path),
                        "database": str(result.database_path),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        destination = (
            arguments.output or Path(settings.DATA_DIR) / "migration" / "captures"
        )
        result = capture_legacy_installation(
            source,
            destination,
            database=arguments.database,
        )
        verification = verify_capture(result.path)
        print(json.dumps(verification, indent=2, sort_keys=True))
        return 0

    database = resolve_source(arguments.source, database=arguments.database)
    inspection = inspect_source(database)

    if arguments.command == "inspect":
        print(json.dumps(inspection.as_dict(), indent=2, sort_keys=True))
        return 0

    if inspection.classification != "legacy":
        raise SystemExit(
            "Reconciliation requires a legacy Arthexis database; "
            f"detected {inspection.classification}."
        )

    _setup_django()
    from django.conf import settings

    from arthexis.reconciliation import reconcile
    from arthexis.reconciliation.importer import write_receipt

    report = reconcile(database, dry_run=arguments.command == "dry-run")
    receipt = write_receipt(report, Path(settings.DATA_DIR))
    print(f"Reconciliation receipt: {receipt}")
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
