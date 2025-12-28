#!/usr/bin/env python3
"""Validate that seed fixtures explicitly mark seed data entries."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

import django
from django.apps import apps
from django.conf import settings


def _load_fixture_entries(path: Path) -> list[dict]:
    """Return JSON fixture entries for a given file, ignoring invalid structures."""

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - noisy failure path
        raise ValueError(f"Invalid JSON in fixture {path}") from exc

    if not isinstance(data, list):
        return []

    return [entry for entry in data if isinstance(entry, dict)]


def find_missing_seed_flags(fixtures_root: Path) -> list[tuple[Path, str]]:
    """Find fixture entries missing the ``is_seed_data`` flag.

    Args:
        fixtures_root: Root directory to search for fixture files.

    Returns:
        List of tuples containing the path to the fixture and its model label when
        ``is_seed_data`` is required but not set to ``True``.
    """

    missing: list[tuple[Path, str]] = []

    for path in fixtures_root.rglob("fixtures/*.json"):
        for entry in _load_fixture_entries(path):
            model_label = entry.get("model")
            fields = entry.get("fields", {})
            if not model_label or not isinstance(fields, dict):
                continue

            try:
                model = apps.get_model(model_label)
            except LookupError:
                continue

            has_seed_flag = any(
                field.name == "is_seed_data" for field in model._meta.fields
            )
            if not has_seed_flag:
                continue

            if fields.get("is_seed_data") is not True:
                missing.append((path, model_label))

    return missing


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    django.setup()

    fixtures_root = Path(settings.BASE_DIR) / "apps"
    missing_flags = find_missing_seed_flags(fixtures_root)

    if missing_flags:
        print("Seed data flags missing in fixtures:", file=sys.stderr)
        for path, model_label in missing_flags:
            relative = path.relative_to(REPO_ROOT)
            print(f"- {relative}: {model_label}", file=sys.stderr)
        return 1

    print("Seed fixture lint passed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
