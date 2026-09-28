#!/usr/bin/env python3
"""Capture, restore, and reconcile legacy Arthexis data from Arthexis 2.0."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from arthexis.reconciliation.source import inspect_source, resolve_source

REHEARSE_DOC_PATH = "docs/legacy-satellite-migration.md"
REHEARSE_DOC_URL = (
    "https://github.com/arthexis/arthexis/blob/main/"
    "docs/legacy-satellite-migration.md"
)


def _rehearse_help() -> str:
    return f"""Legacy satellite migration rehearsal

Usage:
  python scripts/reconcile.py rehearse <legacy-installation> [options]
  python scripts/reconcile.py rehearse <legacy-installation> --cutover [options]

Ordinary rehearsal:
  Captures a consistent read-only SQLite snapshot from the still-running legacy
  installation, then restores, reconciles, verifies, and reports from the
  immutable capture. The legacy installation remains authoritative.

Final cutover rehearsal:
  Add --cutover to require the no-missed-writes proof before a final GO bundle
  can be accepted. The authority switch itself belongs to issue #278.

Important options:
  --output PATH               rehearsal workspace root
  --database PATH             explicit legacy SQLite database
  --batch-size N              reconciliation batch size
  --max-elapsed-seconds N     hard elapsed-time safety limit
  --max-peak-rss-mib N        hard peak-memory safety limit
  --max-workspace-mib N       hard workspace-size safety limit
  --min-free-disk-mib N       minimum free disk required before capture
  --cutover                   final cutover rehearsal mode

Operator procedure:
  {REHEARSE_DOC_PATH}
  {REHEARSE_DOC_URL}
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "capture",
            "verify",
            "restore",
            "preserve",
            "reconcile-fixture",
            "verify-migration",
            "rehearse",
            "inspect",
            "dry-run",
            "import",
        ),
        help="capture/restore a legacy source, run a rehearsal, or run reconciliation",
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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        help="maximum legacy rows fetched into memory at once (default: 250)",
    )
    parser.add_argument(
        "--nice",
        type=int,
        default=10,
        help="POSIX process niceness increment for local reconciliation (default: 10)",
    )
    parser.add_argument(
        "--max-elapsed-seconds",
        type=float,
        default=1800.0,
        help="hard rehearsal elapsed-time limit (default: 1800)",
    )
    parser.add_argument(
        "--max-peak-rss-mib",
        type=float,
        default=512.0,
        help="hard rehearsal peak-RSS limit in MiB (default: 512)",
    )
    parser.add_argument(
        "--max-workspace-mib",
        type=float,
        default=2048.0,
        help="hard rehearsal workspace-size limit in MiB (default: 2048)",
    )
    parser.add_argument(
        "--min-free-disk-mib",
        type=float,
        default=1024.0,
        help="minimum free disk required before capture in MiB (default: 1024)",
    )
    parser.add_argument(
        "--cutover",
        action="store_true",
        help=(
            "treat rehearsal as the final cutover run and require proof that "
            "the live legacy database did not change after the initial capture"
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


def _reconcile_fixture(arguments: argparse.Namespace, *, emit: bool = True) -> int:
    source = _require_source(arguments.source).expanduser().resolve()
    if arguments.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1.")
    if arguments.nice < 0:
        raise SystemExit("--nice cannot be negative.")
    if arguments.nice and hasattr(os, "nice"):
        os.nice(arguments.nice)
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
        report, receipt = reconcile_fixture(
            source,
            destination,
            batch_size=arguments.batch_size,
        )
    except Exception as error:
        diagnostic = write_failure_receipt(source, error)
        if diagnostic is not None:
            print(f"Reconciliation diagnostic: {diagnostic}", file=sys.stderr)
        raise

    if emit:
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



def _rehearse(arguments: argparse.Namespace) -> int:
    """Run capture -> restore -> reconcile -> verify from one live legacy source."""

    started = time.monotonic()
    source = _require_source(arguments.source).expanduser().resolve()
    if arguments.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1.")
    if arguments.nice < 0:
        raise SystemExit("--nice cannot be negative.")
    resource_values = (
        arguments.max_elapsed_seconds,
        arguments.max_peak_rss_mib,
        arguments.max_workspace_mib,
        arguments.min_free_disk_mib,
    )
    if any(value <= 0 for value in resource_values):
        raise SystemExit("Resource safety limits must be greater than zero.")

    data_dir = Path(
        os.environ.get("ARTHEXIS_DATA_DIR", str(PROJECT_ROOT / "var"))
    ).expanduser().resolve()
    rehearsal_root = (
        arguments.output.expanduser().resolve()
        if arguments.output
        else data_dir / "migration" / "rehearsals"
    )
    captures = rehearsal_root / "captures"
    fixtures = rehearsal_root / "fixtures"

    from arthexis.reconciliation.capture import (
        capture_legacy_installation,
        verify_capture,
    )
    from arthexis.reconciliation.fixture import restore_fixture
    from arthexis.reconciliation.rehearsal import (
        ResourcePolicy,
        create_go_bundle,
        evaluate_resources,
        preflight_disk,
        verify_cutover_source_unchanged,
        write_resource_receipt,
    )
    from arthexis.reconciliation.verification import verify_reconciliation

    resource_policy = ResourcePolicy(
        max_elapsed_seconds=arguments.max_elapsed_seconds,
        max_peak_rss_mib=arguments.max_peak_rss_mib,
        max_workspace_mib=arguments.max_workspace_mib,
        min_free_disk_mib=arguments.min_free_disk_mib,
    )
    disk_preflight = preflight_disk(rehearsal_root, resource_policy)
    if disk_preflight["violations"]:
        payload = {
            "source": str(source),
            "decision": "NO-GO",
            "reason": "resource-limit",
            "resource_policy": resource_policy.as_dict(),
            "resource_preflight": disk_preflight,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    capture = capture_legacy_installation(
        source,
        captures,
        database=arguments.database,
    )
    capture_verification = verify_capture(capture.path)
    fixture = restore_fixture(capture.path, fixtures)

    arguments.source = fixture.path
    arguments.destination_database = fixture.path / "reconciled.sqlite3"
    # Rehearsal stdout is a machine-readable JSON contract. Some Django
    # management commands emit informational text, so contain that output here.
    with redirect_stdout(io.StringIO()):
        _reconcile_fixture(arguments, emit=False)

    resource_result = evaluate_resources(
        rehearsal_root,
        fixture.path / "reconciliation.json",
        started=started,
        policy=resource_policy,
    )
    resource_receipt = write_resource_receipt(rehearsal_root, resource_result)
    if resource_result["decision"] == "NO-GO":
        payload = {
            "source": str(source),
            "capture": {
                "capture_id": capture.capture_id,
                "path": str(capture.path),
                "manifest_sha256": capture_verification["manifest_sha256"],
                "database_sha256": capture_verification["database_sha256"],
            },
            "fixture": {
                "fixture_id": fixture.fixture_id,
                "path": str(fixture.path),
            },
            "destination_database": str(fixture.path / "reconciled.sqlite3"),
            "decision": "NO-GO",
            "reason": "resource-limit",
            "resource_report": str(resource_receipt),
            "resource_safety": resource_result,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    verification = verify_reconciliation(fixture.path)
    cutover_proof = None
    if verification.decision == "GO" and arguments.cutover:
        cutover_proof = verify_cutover_source_unchanged(
            source,
            rehearsal_root,
            expected_database_sha256=capture_verification["database_sha256"],
            database=arguments.database,
        )
        if cutover_proof["decision"] != "GO":
            payload = {
                "source": str(source),
                "capture": {
                    "capture_id": capture.capture_id,
                    "path": str(capture.path),
                    "manifest_sha256": capture_verification["manifest_sha256"],
                    "database_sha256": capture_verification["database_sha256"],
                },
                "fixture": {
                    "fixture_id": fixture.fixture_id,
                    "path": str(fixture.path),
                },
                "destination_database": str(fixture.path / "reconciled.sqlite3"),
                "decision": "NO-GO",
                "reason": "cutover-source-changed",
                "cutover": cutover_proof,
                "resource_report": str(resource_receipt),
                "resource_safety": resource_result,
                "json_report": str(verification.json_path),
                "text_report": str(verification.text_path),
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2

    go_bundle = None
    if verification.decision == "GO":
        go_bundle = create_go_bundle(
            rehearsal_root,
            capture_path=capture.path,
            fixture_path=fixture.path,
            migration_report=verification.json_path,
            migration_text_report=verification.text_path,
            resource_report=resource_receipt,
            cutover=cutover_proof,
        )
    payload = {
        "source": str(source),
        "capture": {
            "capture_id": capture.capture_id,
            "path": str(capture.path),
            "manifest_sha256": capture_verification["manifest_sha256"],
            "database_sha256": capture_verification["database_sha256"],
        },
        "fixture": {
            "fixture_id": fixture.fixture_id,
            "path": str(fixture.path),
        },
        "destination_database": str(fixture.path / "reconciled.sqlite3"),
        "decision": verification.decision,
        "cutover": cutover_proof,
        "go_bundle": str(go_bundle) if go_bundle is not None else None,
        "resource_report": str(resource_receipt),
        "resource_safety": resource_result,
        "json_report": str(verification.json_path),
        "text_report": str(verification.text_path),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if verification.decision == "GO" else 2

def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "rehearse" and sys.argv[2] in {"-h", "--help"}:
        print(_rehearse_help())
        return 0

    arguments = _parser().parse_args()

    if arguments.command == "reconcile-fixture":
        return _reconcile_fixture(arguments)

    if arguments.command == "rehearse":
        return _rehearse(arguments)

    if arguments.command == "verify-migration":
        source = _require_source(arguments.source)
        from arthexis.reconciliation.verification import verify_reconciliation

        result = verify_reconciliation(source)
        print(
            json.dumps(
                {
                    "decision": result.decision,
                    "json_report": str(result.json_path),
                    "text_report": str(result.text_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if result.decision == "GO" else 2

    if arguments.command in {"capture", "verify", "restore", "preserve"}:
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

        if arguments.command == "preserve":
            from arthexis.reconciliation.preservation import preserve_capture

            destination = (
                arguments.output
                or Path(settings.DATA_DIR) / "migration" / "preserved"
            )
            result = preserve_capture(source, destination)
            print(
                json.dumps(
                    {
                        "capture_id": result.capture_id,
                        "path": str(result.path),
                        "database": str(result.database_path),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

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
