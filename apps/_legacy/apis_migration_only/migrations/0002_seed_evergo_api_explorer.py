"""Seed API explorer with Evergo endpoints used by integration models."""

from django.db import migrations

from ._utils import load_fixture_payload, resolve_resource_method_api_field_name


EVERGO_API_NAME = "Evergo API"
EVERGO_API_BASE_URL = "https://portal-backend.evergo.com/api/mex/v1/"
EVERGO_API_DESCRIPTION = "Evergo portal backend endpoints used by the Evergo integration."


def seed_evergo_api_explorer(apps, schema_editor):
    """Seed Evergo API explorer only when it does not already exist."""

    del schema_editor
    APIExplorer = apps.get_model("apis", "APIExplorer")
    ResourceMethod = apps.get_model("apis", "ResourceMethod")

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

    api_field_name = resolve_resource_method_api_field_name(ResourceMethod)
    endpoint_fields = load_fixture_payload()
    for endpoint in endpoint_fields:
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
        ("apis", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_evergo_api_explorer, migrations.RunPython.noop),
    ]
