"""Expand Evergo API explorer endpoint catalog from fixture definitions."""

import json
from pathlib import Path

from django.db import migrations


EVERGO_API_NAME = "Evergo API"


def _load_fixture_payload() -> tuple[dict, ...]:
    """Load Evergo endpoint definitions from fixture data."""
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "apis__evergo_endpoints.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return tuple(entry["fields"] for entry in payload if entry.get("model") == "apis.resourcemethod")


def seed_missing_evergo_api_methods(apps, schema_editor):
    """Create any fixture-defined Evergo API methods not yet present in the explorer."""
    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    api = APIExplorer.objects.filter(name=EVERGO_API_NAME).first()
    if api is None:
        return

    for endpoint in _load_fixture_payload():
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


def unseed_added_evergo_api_methods(apps, schema_editor):
    """Reverse only the new endpoints introduced by this expansion migration."""
    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    added_paths = {
        "/user",
        "/ordenes/{order_id}",
        "/ordenes/kit-cfe/{order_id}",
        "/config/catalogs/instaladores/empresas/coord-para-asignar",
        "/reportes/ordenes/{order_id}/visita-tecnica/cuestionario-preguntas",
        "/users/reassignment_reason",
        "/users/crew-people",
    }
    api = APIExplorer.objects.filter(name=EVERGO_API_NAME).first()
    if api is None:
        return

    ResourceMethod.objects.filter(api=api, resource_path__in=added_paths).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0002_seed_evergo_api_explorer"),
    ]

    operations = [
        migrations.RunPython(seed_missing_evergo_api_methods, unseed_added_evergo_api_methods),
    ]
