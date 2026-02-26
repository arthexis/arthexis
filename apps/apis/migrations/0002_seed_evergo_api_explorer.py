"""Seed API explorer with Evergo endpoints used by integration models."""

import json
from pathlib import Path

from django.db import migrations


EVERGO_API_NAME = "Evergo API"
EVERGO_API_BASE_URL = "https://portal-backend.evergo.com/api/mex/v1/"
EVERGO_API_DESCRIPTION = "Evergo portal backend endpoints used by the Evergo integration."


def _load_fixture_payload() -> tuple[dict, ...]:
    """Load Evergo endpoint definitions from fixture data."""

    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "apis__evergo_endpoints.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    endpoint_fields = tuple(
        entry["fields"]
        for entry in payload
        if entry.get("model") == "apis.resourcemethod"
    )
    return endpoint_fields


def seed_evergo_api_explorer(apps, schema_editor):
    """Seed Evergo API explorer only when it does not already exist."""

    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    endpoint_fields = _load_fixture_payload()
    api, created = APIExplorer.objects.get_or_create(
        name=EVERGO_API_NAME,
        defaults={
            "base_url": EVERGO_API_BASE_URL,
            "description": EVERGO_API_DESCRIPTION,
            "is_active": True,
        },
    )
    if not created:
        return

    for endpoint in endpoint_fields:
        ResourceMethod.objects.get_or_create(
            api=api,
            operation_name=endpoint["operation_name"],
            resource_path=endpoint["resource_path"],
            http_method=endpoint["http_method"],
            defaults={
                "request_structure": endpoint["request_structure"],
                "response_structure": endpoint["response_structure"],
                "notes": endpoint["notes"],
            },
        )


def unseed_evergo_api_explorer(apps, schema_editor):
    """Delete Evergo endpoint resource methods and remove API explorer when empty."""

    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    api = APIExplorer.objects.filter(name=EVERGO_API_NAME).first()
    if api is None:
        return

    endpoint_fields = _load_fixture_payload()
    for endpoint in endpoint_fields:
        ResourceMethod.objects.filter(
            api=api,
            operation_name=endpoint["operation_name"],
            resource_path=endpoint["resource_path"],
            http_method=endpoint["http_method"],
        ).delete()

    if not ResourceMethod.objects.filter(api=api).exists():
        api.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_evergo_api_explorer, unseed_evergo_api_explorer),
    ]
