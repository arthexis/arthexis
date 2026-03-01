#!/usr/bin/env python3
"""Validate that seed fixtures explicitly mark seed data entries."""

from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))



def _load_fixture_entries(path: Path) -> list[dict]:
    """Return JSON fixture entries for a given file, ignoring invalid structures."""

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - noisy failure path
        raise ValueError(f"Invalid JSON in fixture {path}") from exc

    if not isinstance(data, list):
        return []

    return [entry for entry in data if isinstance(entry, dict)]


def _iter_fixture_files(fixtures_root: Path, fixture_paths: list[Path] | None = None) -> list[Path]:
    """Return fixture files to lint.

    Args:
        fixtures_root: Root directory used for repository scans.
        fixture_paths: Explicit fixture paths to lint. When omitted, all fixture
            JSON files under ``fixtures_root`` are scanned.

    Returns:
        A list of fixture files to inspect.
    """

    if fixture_paths is None:
        return sorted(fixtures_root.rglob("fixtures/*.json"))

    return sorted(path.resolve() for path in fixture_paths)


def find_missing_seed_flags(
    fixtures_root: Path,
    fixture_paths: list[Path] | None = None,
) -> list[tuple[Path, str]]:
    """Find fixture entries missing the ``is_seed_data`` flag.

    Args:
        fixtures_root: Root directory to search for fixture files.
        fixture_paths: Optional fixture file paths to lint instead of scanning the
            whole repository.

    Returns:
        List of tuples containing the path to the fixture and its model label when
        ``is_seed_data`` is required but not set to ``True``.
    """

    from django.apps import apps

    missing: list[tuple[Path, str]] = []

    for path in _iter_fixture_files(fixtures_root, fixture_paths):
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for fixture linting."""

    parser = argparse.ArgumentParser(
        description="Validate that fixture seed data sets is_seed_data=true.",
    )
    parser.add_argument(
        "paths",
        metavar="PATH",
        nargs="*",
        help="Explicit fixture JSON files to lint. Defaults to scanning apps/**/fixtures/*.json.",
    )
    return parser.parse_args(argv)


def _resolve_fixture_paths(paths: list[str]) -> list[Path]:
    """Resolve and validate explicit fixture paths.

    Args:
        paths: Fixture paths passed on the command line.

    Returns:
        Resolved fixture paths.

    Raises:
        FileNotFoundError: If any provided path does not exist.
    """

    resolved_paths: list[Path] = []
    for path_str in paths:
        path = Path(path_str)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Fixture path does not exist: {path_str}")
        resolved_paths.append(path)

    return resolved_paths


def main(argv: list[str] | None = None) -> int:
    """Run fixture linting and return an exit code."""

    from django.conf import settings
    import django

    args = _parse_args(argv)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    django.setup()

    fixtures_root = Path(settings.BASE_DIR) / "apps"
    try:
        explicit_paths = _resolve_fixture_paths(args.paths) if args.paths else None
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    missing_flags = find_missing_seed_flags(fixtures_root, explicit_paths)

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
