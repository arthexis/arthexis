from django.db import migrations, models


def delete_recipe_dashboard_actions(apps, schema_editor):
    """Delete dashboard actions that still target the retired recipe mode."""

    DashboardAction = apps.get_model("actions", "DashboardAction")
    DashboardAction.objects.filter(target_type="recipe").delete()


def restore_recipe_dashboard_actions(apps, schema_editor):
    """No-op reverse for deleted recipe dashboard actions."""


class Migration(migrations.Migration):

    dependencies = [
        ("actions", "0007_rebrand_suite_tasks_to_task_panels"),
    ]

    operations = [
        migrations.RunPython(delete_recipe_dashboard_actions, restore_recipe_dashboard_actions),
        migrations.RemoveField(
            model_name="dashboardaction",
            name="recipe",
        ),
        migrations.AlterField(
            model_name="dashboardaction",
            name="target_type",
            field=models.CharField(
                choices=[("admin_url", "Admin URL Name"), ("absolute_url", "Absolute URL")],
                default="admin_url",
                max_length=24,
            ),
        ),
        migrations.RemoveField(
            model_name="remoteaction",
            name="recipe",
        ),
    ]
