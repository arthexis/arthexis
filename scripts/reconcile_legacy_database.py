#!/usr/bin/env python3
"""Import selected Arthexis 1.x logical records into an initialized 2.0 DB."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="read-only legacy SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="rollback target writes")
    arguments = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")
    import django

    django.setup()
    from arthexis.reconciliation import reconcile
    from arthexis.reconciliation.importer import write_receipt

    report = reconcile(arguments.source, dry_run=arguments.dry_run)
    data_dir = Path(os.environ["ARTHEXIS_DATA_DIR"])
    receipt = write_receipt(report, data_dir)
    print(f"Reconciliation receipt: {receipt}")
    print(report.as_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
