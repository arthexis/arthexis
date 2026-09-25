#!/usr/bin/env python3
"""Capture, inspect, verify, and reconcile legacy Arthexis data from Arthexis 2.0."""

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
        choices=("capture", "verify", "inspect", "dry-run", "import"),
        help="capture/verify a passive source, inspect it, or reconcile it",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="legacy Arthexis installation, SQLite database, or capture bundle",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="explicit legacy SQLite database path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="capture destination root (defaults under ARTHEXIS_DATA_DIR)",
    )
    return parser


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")
    import django

    django.setup()


def _require_source(source: Path | None) -> Path:
    if source is None:
        raise SystemExit("Provide a legacy Arthexis installation path.")
    return source


def main() -> int:
    arguments = _parser().parse_args()

    if arguments.command in {"capture", "verify"}:
        from arthexis.reconciliation.capture import (
            capture_legacy_installation,
            verify_capture,
        )

        source = _require_source(arguments.source)
        if arguments.command == "verify":
            result = verify_capture(source)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        _setup_django()
        from django.conf import settings

        destination = arguments.output or Path(settings.DATA_DIR) / "migration" / "captures"
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
