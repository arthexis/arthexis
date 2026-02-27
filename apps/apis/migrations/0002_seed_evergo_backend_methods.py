"""Seed Evergo backend endpoints in API Explorer from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


EXPLORER_NAME = "Evergo Backend (MEX v1)"
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "apis__evergo_backend_methods.json"
)


def _load_fixture_entries() -> list[dict]:
    """Load API explorer fixture entries for Evergo backend methods."""
    if not FIXTURE_PATH.exists():
        return []
    try:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def seed_evergo_backend_methods(apps, schema_editor):
    """Create or update API explorer and resource methods for Evergo backend."""
    del schema_editor

    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    explorer_defaults = {
        "base_url": "https://portal-backend.evergo.com/api/mex/v1",
        "description": "Catalog of known Evergo backend endpoints observed from portal flows.",
        "is_active": True,
    }
    explorer, _ = APIExplorer.objects.update_or_create(
        name=EXPLORER_NAME,
        defaults=explorer_defaults,
    )

    for entry in _load_fixture_entries():
        if not isinstance(entry, dict):
            continue
        if entry.get("model") != "apis.resourcemethod":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue

        operation_name = str(fields.get("operation_name") or "").strip()
        resource_path = str(fields.get("resource_path") or "").strip()
        http_method = str(fields.get("http_method") or "").strip().upper()
        if not operation_name or not resource_path or not http_method:
            continue

        request_structure = fields.get("request_structure")
        response_structure = fields.get("response_structure")

        ResourceMethod.objects.update_or_create(
            api=explorer,
            operation_name=operation_name,
            resource_path=resource_path,
            http_method=http_method,
            defaults={
                "request_structure": request_structure if request_structure is not None else {},
                "response_structure": response_structure if response_structure is not None else {},
                "notes": str(fields.get("notes") or ""),
            },
        )


def unseed_evergo_backend_methods(apps, schema_editor):
    """Remove Evergo backend API explorer seed records."""
    del schema_editor

    APIExplorer = apps.get_model("apis", "APIExplorer")
    APIExplorer.objects.filter(name=EXPLORER_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_evergo_backend_methods, unseed_evergo_backend_methods),
    ]
