"""Expand Evergo API explorer endpoint catalog from fixture definitions."""

from django.db import migrations

from ._utils import load_fixture_payload, resolve_resource_method_api_field_name


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


def _load_seed_endpoints() -> tuple[dict, ...]:
    """Return only the endpoint definitions seeded by this migration."""

    selected = []
    for endpoint in load_fixture_payload():
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

    api_field_name = resolve_resource_method_api_field_name(ResourceMethod)
    for endpoint in _load_seed_endpoints():
        lookup = {
            api_field_name: api,
            "operation_name": endpoint["operation_name"],
            "resource_path": endpoint["resource_path"],
            "http_method": endpoint["http_method"],
        }
        ResourceMethod.objects.get_or_create(
            **lookup,
            defaults={
                "request_structure": endpoint["request_structure"],
                "response_structure": endpoint["response_structure"],
                "notes": endpoint["notes"],
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0002_seed_evergo_api_explorer"),
    ]

    operations = [
        migrations.RunPython(seed_missing_evergo_api_methods, migrations.RunPython.noop),
    ]
