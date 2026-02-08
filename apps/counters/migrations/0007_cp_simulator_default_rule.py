from django.db import migrations


def create_cp_simulator_default_rule(apps, schema_editor):
    """Create the dashboard rule for missing default CP simulators."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Simulator = apps.get_model("ocpp", "Simulator")

    content_type = ContentType.objects.get_for_model(
        Simulator, for_concrete_model=False
    )

    DashboardRule.objects.update_or_create(
        content_type=content_type,
        defaults={
            "name": "CP simulator default",
            "implementation": "python",
            "function_name": "evaluate_cp_simulator_default_rules",
            "success_message": "All rules met.",
            "failure_message": "",
        },
    )


def remove_cp_simulator_default_rule(apps, schema_editor):
    """Remove the dashboard rule for missing default CP simulators."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Simulator = apps.get_model("ocpp", "Simulator")

    content_type = ContentType.objects.get_for_model(
        Simulator, for_concrete_model=False
    )
    DashboardRule.objects.filter(content_type=content_type).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("counters", "0006_user_story_dashboard_rule"),
        ("ocpp", "0020_charger_ftp_server"),
    ]

    operations = [
        migrations.RunPython(
            create_cp_simulator_default_rule,
            reverse_code=remove_cp_simulator_default_rule,
        ),
    ]
