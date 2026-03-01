from django.db import migrations


def create_watchtower_aws_credentials_rule(apps, schema_editor):
    """Create the dashboard rule that requires AWS credentials for Watchtower."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    AWSCredentials = apps.get_model("aws", "AWSCredentials")

    content_type = ContentType.objects.get_for_model(
        AWSCredentials, for_concrete_model=False
    )

    DashboardRule.objects.update_or_create(
        content_type=content_type,
        defaults={
            "name": "AWS credentials readiness",
            "implementation": "python",
            "function_name": "evaluate_aws_credentials_rules",
            "success_message": "All rules met.",
            "failure_message": "",
        },
    )


def remove_watchtower_aws_credentials_rule(apps, schema_editor):
    """Remove the dashboard rule for Watchtower AWS credential readiness."""

    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    AWSCredentials = apps.get_model("aws", "AWSCredentials")

    content_type = ContentType.objects.get_for_model(
        AWSCredentials, for_concrete_model=False
    )
    DashboardRule.objects.filter(content_type=content_type).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("aws", "0001_initial"),
        ("counters", "0007_cp_simulator_default_rule"),
    ]

    operations = [
        migrations.RunPython(
            create_watchtower_aws_credentials_rule,
            reverse_code=remove_watchtower_aws_credentials_rule,
        ),
    ]
