from django.db import migrations


DEFAULT_STAFF_TASKS = (
    {
        "slug": "actions",
        "label": "Actions",
        "description": "Open personal action OpenAPI and remote action tooling.",
        "admin_url_name": "admin:actions_remoteaction_my_openapi_spec",
        "order": 10,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "config",
        "label": "Config",
        "description": "Open configuration shortcuts.",
        "admin_url_name": "admin:config",
        "order": 20,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "data",
        "label": "Data",
        "description": "Manage personal admin data and preferences.",
        "admin_url_name": "admin:user_data",
        "order": 30,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "discover",
        "label": "Discover",
        "description": "Run node and integration discovery tools.",
        "admin_url_name": "admin:nodes_nodefeature_discover",
        "order": 40,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "environment",
        "label": "Environment",
        "description": "Inspect deployment environment details.",
        "admin_url_name": "admin:environment",
        "order": 50,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "logs",
        "label": "Logs",
        "description": "Browse system and application logs.",
        "admin_url_name": "admin:log_viewer",
        "order": 60,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "rules",
        "label": "Rules",
        "description": "Review dashboard rule evaluation outcomes.",
        "admin_url_name": "admin:system-dashboard-rules-report",
        "order": 70,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "seed",
        "label": "Seed",
        "description": "Load baseline data into the system.",
        "admin_url_name": "admin:seed_data",
        "order": 80,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "sigil",
        "label": "Sigil",
        "description": "Build and inspect sigils.",
        "admin_url_name": "admin:sigil_builder",
        "order": 90,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "tasks",
        "label": "Tasks",
        "description": "Open the staff tasks overview and toggles.",
        "admin_url_name": "admin:system",
        "order": 100,
        "default_enabled": True,
        "superuser_only": False,
    },
    {
        "slug": "upgrade",
        "label": "Upgrade",
        "description": "View upgrade status and run upgrade checks.",
        "admin_url_name": "admin:system-upgrade-report",
        "order": 110,
        "default_enabled": True,
        "superuser_only": True,
    },
)


def seed_staff_tasks(apps, schema_editor):
    StaffTask = apps.get_model("actions", "StaffTask")
    for task in DEFAULT_STAFF_TASKS:
        StaffTask.objects.update_or_create(
            slug=task["slug"],
            defaults={
                "label": task["label"],
                "description": task["description"],
                "admin_url_name": task["admin_url_name"],
                "order": task["order"],
                "default_enabled": task["default_enabled"],
                "staff_only": True,
                "superuser_only": task["superuser_only"],
                "is_active": True,
            },
        )


def unseed_staff_tasks(apps, schema_editor):
    StaffTask = apps.get_model("actions", "StaffTask")
    StaffTask.objects.filter(slug__in=[task["slug"] for task in DEFAULT_STAFF_TASKS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("actions", "0003_stafftask_stafftaskpreference"),
    ]

    operations = [
        migrations.RunPython(seed_staff_tasks, unseed_staff_tasks),
    ]
