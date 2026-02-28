from django.db import migrations


RULE_NAME = "Watchtower AWS credentials"
FUNCTION_NAME = "evaluate_aws_credentials_rules"
SUCCESS_MESSAGE = "All rules met."


def seed_watchtower_credentials_rule(apps, schema_editor):
    """Create the dashboard rule that checks Watchtower AWS credentials."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    AWSCredentials = apps.get_model("aws", "AWSCredentials")

    content_type = ContentType.objects.get_for_model(
        AWSCredentials,
        for_concrete_model=False,
    )
    DashboardRule.objects.update_or_create(
        content_type=content_type,
        defaults={
            "name": RULE_NAME,
            "implementation": "python",
            "function_name": FUNCTION_NAME,
            "success_message": SUCCESS_MESSAGE,
            "failure_message": "",
        },
    )


def remove_watchtower_credentials_rule(apps, schema_editor):
    """Remove the dashboard rule that checks Watchtower AWS credentials."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    AWSCredentials = apps.get_model("aws", "AWSCredentials")

    content_type = ContentType.objects.get_for_model(
        AWSCredentials,
        for_concrete_model=False,
    )
    DashboardRule.objects.filter(content_type=content_type, function_name=FUNCTION_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("aws", "0001_initial"),
        ("counters", "0007_cp_simulator_default_rule"),
    ]

    operations = [
        migrations.RunPython(
            seed_watchtower_credentials_rule,
            reverse_code=remove_watchtower_credentials_rule,
        ),
    ]
