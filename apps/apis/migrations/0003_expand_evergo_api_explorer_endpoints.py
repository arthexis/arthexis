"""Expand Evergo API explorer endpoint catalog from fixture definitions."""

import json
from pathlib import Path

from django.db import migrations


EVERGO_API_NAME = "Evergo API"
SEEDED_ENDPOINT_KEYS = {
    ("Get Current User", "/user", "GET"),
    ("Get Order Detail", "/ordenes/{order_id}", "GET"),
    ("Get Order Kit CFE", "/ordenes/kit-cfe/{order_id}", "GET"),
    ("List Assignment Crews", "/config/catalogs/instaladores/empresas/coord-para-asignar", "GET"),
    ("Get Technical Visit Questions", "/reportes/ordenes/{order_id}/visita-tecnica/cuestionario-preguntas", "GET"),
    ("List Reassignment Reasons", "/users/reassignment_reason", "GET"),
    ("List Crew People", "/users/crew-people", "GET"),
}


def _load_fixture_payload() -> tuple[dict, ...]:
    """Load Evergo endpoint definitions from fixture data."""
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "apis__evergo_endpoints.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return tuple(entry["fields"] for entry in payload if entry.get("model") == "apis.resourcemethod")


def _load_seed_endpoints() -> tuple[dict, ...]:
    """Return only the endpoint definitions seeded by this migration."""
    selected = []
    for endpoint in _load_fixture_payload():
        endpoint_key = (endpoint["operation_name"], endpoint["resource_path"], endpoint["http_method"])
        if endpoint_key in SEEDED_ENDPOINT_KEYS:
            selected.append(endpoint)
    return tuple(selected)


def seed_missing_evergo_api_methods(apps, schema_editor):
    """Create any fixture-defined Evergo API methods not yet present in the explorer."""
    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    api = APIExplorer.objects.filter(name=EVERGO_API_NAME).first()
    if api is None:
        return

    for endpoint in _load_seed_endpoints():
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
    """Reverse only the exact endpoints introduced by this expansion migration."""
    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    api = APIExplorer.objects.filter(name=EVERGO_API_NAME).first()
    if api is None:
        return

    for endpoint in _load_seed_endpoints():
        ResourceMethod.objects.filter(
            api=api,
            operation_name=endpoint["operation_name"],
            resource_path=endpoint["resource_path"],
            http_method=endpoint["http_method"],
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0002_seed_evergo_api_explorer"),
    ]

    operations = [
        migrations.RunPython(seed_missing_evergo_api_methods, unseed_added_evergo_api_methods),
    ]
