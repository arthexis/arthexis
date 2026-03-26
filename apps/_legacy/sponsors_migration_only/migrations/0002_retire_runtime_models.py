"""Retire sponsors runtime models without dropping historical tables."""

from django.db import migrations

SPONSOR_RENEWAL_TASK_NAME = "sponsor-renewals"
SPONSOR_RENEWAL_TASK_PATH = (
    "apps._legacy.sponsors_migration_only.tasks.process_sponsorship_renewals"
)
LEGACY_SPONSOR_RENEWAL_TASK_PATHS = (
    SPONSOR_RENEWAL_TASK_PATH,
    "apps.sponsors.tasks.process_sponsorship_renewals",
)
SPONSOR_PERMISSION_NAMES = {
    "sponsorship": {
        "add_sponsorship": "Can add Sponsorship",
        "change_sponsorship": "Can change Sponsorship",
        "delete_sponsorship": "Can delete Sponsorship",
        "view_sponsorship": "Can view Sponsorship",
    },
    "sponsorshippayment": {
        "add_sponsorshippayment": "Can add Sponsorship payment",
        "change_sponsorshippayment": "Can change Sponsorship payment",
        "delete_sponsorshippayment": "Can delete Sponsorship payment",
        "view_sponsorshippayment": "Can view Sponsorship payment",
    },
    "sponsortier": {
        "add_sponsortier": "Can add Sponsor tier",
        "change_sponsortier": "Can change Sponsor tier",
        "delete_sponsortier": "Can delete Sponsor tier",
        "view_sponsortier": "Can view Sponsor tier",
    },
}


def delete_retired_sponsor_task(apps, schema_editor):
    """Delete legacy Celery beat entries for the retired sponsor renewal task.

    Parameters:
        apps (StateApps): Historical app registry supplied by Django migrations.
        schema_editor (BaseDatabaseSchemaEditor): Active migration schema editor.

    Returns:
        None.

    Raises:
        LookupError: Propagates when the historical periodic task model is unavailable.
    """

    del schema_editor

    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    PeriodicTask.objects.filter(name=SPONSOR_RENEWAL_TASK_NAME).delete()
    for task_path in LEGACY_SPONSOR_RENEWAL_TASK_PATHS:
        PeriodicTask.objects.filter(task=task_path).delete()


def restore_retired_sponsor_task(apps, schema_editor):
    """Recreate the sponsor renewal beat entry when rolling back retirement.

    Parameters:
        apps (StateApps): Historical app registry supplied by Django migrations.
        schema_editor (BaseDatabaseSchemaEditor): Active migration schema editor.

    Returns:
        None.

    Raises:
        LookupError: Propagates when historical Celery beat models are unavailable.
    """

    del schema_editor

    try:
        IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=1,
        period="hours",
    )
    PeriodicTask.objects.update_or_create(
        name=SPONSOR_RENEWAL_TASK_NAME,
        defaults={
            "interval": schedule,
            "task": SPONSOR_RENEWAL_TASK_PATH,
        },
    )


def delete_retired_sponsor_content_types(apps, schema_editor):
    """Remove stale content types and permissions for retired sponsor models.

    Parameters:
        apps (StateApps): Historical app registry supplied by Django migrations.
        schema_editor (BaseDatabaseSchemaEditor): Active migration schema editor.

    Returns:
        None.

    Raises:
        Exception: Propagates migration database errors from Django ORM operations.
    """

    del schema_editor

    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for model_name in SPONSOR_PERMISSION_NAMES:
        content_type = ContentType.objects.filter(
            app_label="sponsors",
            model=model_name,
        ).first()
        if content_type is None:
            continue
        Permission.objects.filter(content_type=content_type).delete()
        content_type.delete()


def restore_retired_sponsor_content_types(apps, schema_editor):
    """Recreate sponsor content types and permissions during migration rollback.

    Parameters:
        apps (StateApps): Historical app registry supplied by Django migrations.
        schema_editor (BaseDatabaseSchemaEditor): Active migration schema editor.

    Returns:
        None.

    Raises:
        Exception: Propagates migration database errors from Django ORM operations.
    """

    del schema_editor

    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for model_name, permission_names in SPONSOR_PERMISSION_NAMES.items():
        content_type, _ = ContentType.objects.get_or_create(
            app_label="sponsors",
            model=model_name,
        )
        for codename, name in permission_names.items():
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=codename,
                defaults={"name": name},
            )


class Migration(migrations.Migration):
    """Remove the runtime model state while preserving database tables for upgrades."""

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("django_celery_beat", "0001_initial"),
        ("sponsors", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    delete_retired_sponsor_task,
                    restore_retired_sponsor_task,
                ),
                migrations.RunPython(
                    delete_retired_sponsor_content_types,
                    restore_retired_sponsor_content_types,
                ),
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="sponsorshippayment",
                    name="processor_content_type",
                ),
                migrations.RemoveField(
                    model_name="sponsorshippayment",
                    name="sponsorship",
                ),
                migrations.RemoveField(
                    model_name="sponsortier",
                    name="security_groups",
                ),
                migrations.DeleteModel(name="Sponsorship"),
                migrations.DeleteModel(name="SponsorshipPayment"),
                migrations.DeleteModel(name="SponsorTier"),
            ],
        ),
    ]
