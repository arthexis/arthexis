"""Seed Evergo portal backend endpoints in API explorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.db import migrations

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "apis__evergo_portal_backend.json"
API_NAME = "Evergo Portal Backend (MEX)"


def _load_fixture_rows() -> list[dict[str, Any]]:
    """Return fixture rows for Evergo API explorer seed data."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def seed_evergo_api_explorer(apps, schema_editor) -> None:
    """Insert/update API explorer + resource methods from fixture."""
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    entries = _load_fixture_rows()
    api_pk_map: dict[int, Any] = {}

    for row in entries:
        if row.get("model") != "apis.apiexplorer":
            continue
        fields = row.get("fields", {})
        stale = APIExplorer.objects.filter(name=fields["name"]).exclude(pk=int(row["pk"]))
        if stale.exists():
            stale.delete()
        api_obj, _ = APIExplorer.objects.update_or_create(
            pk=int(row["pk"]),
            defaults={
                "name": fields["name"],
                "base_url": fields["base_url"],
                "description": fields.get("description", ""),
                "is_active": bool(fields.get("is_active", True)),
            },
        )
        api_pk_map[int(row["pk"])] = api_obj

    for row in entries:
        if row.get("model") != "apis.resourcemethod":
            continue
        fields = row.get("fields", {})
        api_obj = api_pk_map[int(fields["api"])]
        ResourceMethod.objects.update_or_create(
            pk=int(row["pk"]),
            defaults={
                "api": api_obj,
                "resource_path": fields["resource_path"],
                "http_method": fields["http_method"],
                "operation_name": fields["operation_name"],
                "request_structure": fields.get("request_structure") or {},
                "response_structure": fields.get("response_structure") or {},
                "notes": fields.get("notes", ""),
            },
        )


def unseed_evergo_api_explorer(apps, schema_editor) -> None:
    """Remove Evergo API explorer records created by this migration."""
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    api = APIExplorer.objects.filter(name=API_NAME).first()
    if api is None:
        return
    ResourceMethod.objects.filter(api=api).delete()
    api.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_evergo_api_explorer, unseed_evergo_api_explorer),
    ]
