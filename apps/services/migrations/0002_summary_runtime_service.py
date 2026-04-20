from __future__ import annotations

from django.db import migrations


def add_summary_runtime_service(apps, schema_editor) -> None:
    LifecycleService = apps.get_model("services", "LifecycleService")
    LifecycleService.objects.update_or_create(
        slug="summary-runtime",
        defaults={
            "display": "LLM Summary Runtime",
            "unit_template": "summary-runtime-{service}.service",
            "activation": "lockfile",
            "lock_names": ["summary-runtime-service.lck"],
            "sort_order": 40,
        },
    )


def remove_summary_runtime_service(apps, schema_editor) -> None:
    LifecycleService = apps.get_model("services", "LifecycleService")
    LifecycleService.objects.filter(slug="summary-runtime").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_summary_runtime_service, remove_summary_runtime_service),
    ]
