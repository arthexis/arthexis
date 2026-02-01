from django.db import migrations


def create_user_story_dashboard_rule(apps, schema_editor):
    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    UserStory = apps.get_model("pages", "UserStory")

    content_type = ContentType.objects.get_for_model(
        UserStory, for_concrete_model=False
    )

    DashboardRule.objects.update_or_create(
        content_type=content_type,
        defaults={
            "name": "User Story assignments",
            "implementation": "python",
            "function_name": "evaluate_user_story_assignment_rules",
            "success_message": "All rules met.",
            "failure_message": "",
        },
    )


def remove_user_story_dashboard_rule(apps, schema_editor):
    DashboardRule = apps.get_model("counters", "DashboardRule")
    ContentType = apps.get_model("contenttypes", "ContentType")
    UserStory = apps.get_model("pages", "UserStory")

    content_type = ContentType.objects.get_for_model(
        UserStory, for_concrete_model=False
    )
    DashboardRule.objects.filter(content_type=content_type).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("counters", "0005_remove_badgecounter"),
        ("pages", "0009_merge_20260127_1721"),
    ]

    operations = [
        migrations.RunPython(
            create_user_story_dashboard_rule,
            reverse_code=remove_user_story_dashboard_rule,
        ),
    ]
