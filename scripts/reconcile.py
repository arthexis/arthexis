#!/usr/bin/env python3
"""Inspect and reconcile legacy Arthexis databases from Arthexis 2.0."""

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
        choices=("inspect", "dry-run", "import"),
        help="inspect a source, simulate reconciliation, or import it",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="legacy Arthexis installation directory or SQLite database",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="explicit legacy SQLite database path",
    )
    return parser


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")
    import django

    django.setup()


def main() -> int:
    arguments = _parser().parse_args()
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
