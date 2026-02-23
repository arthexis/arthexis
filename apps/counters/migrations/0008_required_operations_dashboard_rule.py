from django.db import migrations


def create_required_operations_dashboard_rule(apps, schema_editor):
    """Create dashboard rule that tracks required operations status."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    OperationScreen = apps.get_model("ops", "OperationScreen")

    content_type = ContentType.objects.get_for_model(
        OperationScreen, for_concrete_model=False
    )

    DashboardRule.objects.update_or_create(
        content_type=content_type,
        defaults={
            "name": "Required operations",
            "implementation": "python",
            "function_name": "evaluate_required_operations_rules",
            "success_message": "All rules met.",
            "failure_message": "",
        },
    )


def remove_required_operations_dashboard_rule(apps, schema_editor):
    """Remove dashboard rule that tracks required operations status."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    OperationScreen = apps.get_model("ops", "OperationScreen")

    content_type = ContentType.objects.get_for_model(
        OperationScreen, for_concrete_model=False
    )
    DashboardRule.objects.filter(content_type=content_type).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0001_initial"),
        ("counters", "0007_cp_simulator_default_rule"),
    ]

    operations = [
        migrations.RunPython(
            create_required_operations_dashboard_rule,
            reverse_code=remove_required_operations_dashboard_rule,
        ),
    ]
