from __future__ import annotations

import json

from apps.ocpp.transactions_io import import_transactions


def run_import_transactions(*, input_path: str) -> int:
    """Import OCPP transactions from a JSON payload file."""
    with open(input_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return import_transactions(data)
