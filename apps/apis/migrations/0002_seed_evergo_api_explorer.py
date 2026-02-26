"""Seed API explorer with Evergo endpoints used by integration models."""

from django.db import migrations


EVERGO_API_NAME = "Evergo API"

EVERGO_ENDPOINTS = (
    {
        "operation_name": "Login",
        "resource_path": "/login",
        "http_method": "POST",
        "request_structure": {"email": "string", "password": "string"},
        "response_structure": {
            "id": "number",
            "name": "string",
            "email": "string",
            "subempresas": [
                {
                    "id": "number",
                    "idInstalaEmpresa": "number",
                    "empresa": "string",
                    "nombre": "string",
                }
            ],
        },
        "notes": "Authenticates contractor credentials and returns profile metadata.",
    },
    {
        "operation_name": "List Sites",
        "resource_path": "/config/catalogs/sitios/all",
        "http_method": "GET",
        "request_structure": {},
        "response_structure": [{"id": "number", "nombre": "string"}],
        "notes": "Returns available site catalog entries.",
    },
    {
        "operation_name": "Search Engineers",
        "resource_path": "/ordenes/search-ingenieros",
        "http_method": "GET",
        "request_structure": {},
        "response_structure": [{"id": "number", "nombre": "string"}],
        "notes": "Returns engineer catalog entries for order assignment.",
    },
    {
        "operation_name": "List Order Statuses",
        "resource_path": "/config/catalogs/orden-estatus",
        "http_method": "GET",
        "request_structure": {},
        "response_structure": [{"id": "number", "nombre": "string"}],
        "notes": "Returns order status catalog entries.",
    },
    {
        "operation_name": "List Coordinator Orders",
        "resource_path": "/ordenes/instalador-coordinador",
        "http_method": "GET",
        "request_structure": {
            "estatus": "number|null",
            "sitio": "number|null",
            "ingeniero": "number|null",
            "busqueda": "string|null",
        },
        "response_structure": {
            "data": [{"id": "number", "numeroOrden": "string"}]
        },
        "notes": "Searches orders for the authenticated installer/coordinator.",
    },
)


def seed_evergo_api_explorer(apps, schema_editor):
    """Create or update the Evergo API explorer and expected endpoint definitions."""

    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

    api, _ = APIExplorer.objects.update_or_create(
        name=EVERGO_API_NAME,
        defaults={
            "base_url": "https://portal-backend.evergo.com/api/mex/v1/",
            "description": "Evergo portal backend endpoints used by the Evergo integration.",
            "is_active": True,
        },
    )

    for endpoint in EVERGO_ENDPOINTS:
        ResourceMethod.objects.update_or_create(
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

    for endpoint in EVERGO_ENDPOINTS:
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
