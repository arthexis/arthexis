from django.db import migrations


CONTROL_LOCAL_SERVICES = (
    {
        "slug": "llm-lcd-summary",
        "display": "LLM LCD Summary",
        "unit_template": "arthexis-llm-lcd-summary.service",
        "unit_kind": "service",
        "docs_path": "services/llm-lcd-summary",
        "activation": "feature",
        "feature_slug": "llm-summary",
        "lock_names": [],
        "sort_order": 50,
    },
    {
        "slug": "llm-lcd-summary-timer",
        "display": "LLM LCD Summary Timer",
        "unit_template": "arthexis-llm-lcd-summary.timer",
        "unit_kind": "timer",
        "docs_path": "services/llm-lcd-summary",
        "activation": "feature",
        "feature_slug": "llm-summary",
        "lock_names": [],
        "sort_order": 51,
    },
    {
        "slug": "usb-inventory",
        "display": "USB Inventory",
        "unit_template": "arthexis-usb-inventory.service",
        "unit_kind": "service",
        "docs_path": "services/usb-inventory",
        "activation": "feature",
        "feature_slug": "usb-inventory",
        "lock_names": [],
        "sort_order": 60,
    },
    {
        "slug": "usb-inventory-timer",
        "display": "USB Inventory Timer",
        "unit_template": "arthexis-usb-inventory.timer",
        "unit_kind": "timer",
        "docs_path": "services/usb-inventory",
        "activation": "feature",
        "feature_slug": "usb-inventory",
        "lock_names": [],
        "sort_order": 61,
    },
)


def seed_control_local_services(apps, schema_editor):
    LifecycleService = apps.get_model("services", "LifecycleService")
    for service in CONTROL_LOCAL_SERVICES:
        slug = service["slug"]
        defaults = {key: value for key, value in service.items() if key != "slug"}
        LifecycleService.objects.update_or_create(slug=slug, defaults=defaults)


def remove_control_local_services(apps, schema_editor):
    LifecycleService = apps.get_model("services", "LifecycleService")
    LifecycleService.objects.filter(
        slug__in=[service["slug"] for service in CONTROL_LOCAL_SERVICES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0002_lifecycleservice_unit_kind"),
    ]

    operations = [
        migrations.RunPython(seed_control_local_services, remove_control_local_services),
    ]
