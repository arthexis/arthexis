from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings


@pytest.mark.django_db
def test_fixtures_mark_seed_data():
    missing: list[tuple[str, str]] = []
    base = Path(settings.BASE_DIR) / "apps"

    for path in base.rglob("fixtures/*.json"):
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            continue

        for entry in data:
            if not isinstance(entry, dict):
                continue
            model_label = entry.get("model")
            fields = entry.get("fields", {})
            if not model_label or not isinstance(fields, dict):
                continue
            try:
                model = apps.get_model(model_label)
            except LookupError:
                continue
            if any(f.name == "is_seed_data" for f in model._meta.fields):
                if fields.get("is_seed_data") is not True:
                    missing.append(
                        (str(path.relative_to(settings.BASE_DIR)), model_label)
                    )

    assert not missing, f"Seed data flags missing in fixtures: {missing}"
