"""Utilities for hydrating suite feature seed fixtures after migrations."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command

from apps.app.models import Application

FEATURE_FIXTURE_GLOB = "features__*.json"


def _fixture_paths() -> list[Path]:
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    return sorted(fixtures_dir.glob(FEATURE_FIXTURE_GLOB))


def _ensure_fixture_applications_exist(*, fixture_paths: list[Path]) -> None:
    labels: set[str] = set()
    for fixture_path in fixture_paths:
        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            main_app = fields.get("main_app")
            if isinstance(main_app, list) and main_app:
                main_app = main_app[0]
            if isinstance(main_app, str) and main_app.strip():
                labels.add(main_app.strip())

    for label in sorted(labels):
        Application.objects.get_or_create(name=label)


def load_feature_seed_data(sender=None, **kwargs) -> None:
    """Hydrate bundled suite feature fixtures during migrate/install flows."""

    del sender
    using = kwargs.get("using") or "default"
    fixture_paths = _fixture_paths()
    if not fixture_paths:
        return
    _ensure_fixture_applications_exist(fixture_paths=fixture_paths)
    call_command(
        "loaddata",
        *(str(path.relative_to(Path(settings.BASE_DIR))) for path in fixture_paths),
        database=using,
        verbosity=0,
    )
