from django.db import migrations, models


CODENAME = "can_trigger_upgrade_checks"
PERMISSION_NAME = "Can trigger upgrade checks"


def _permission_models(apps):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    return ContentType, Permission


def move_upgrade_permission_to_release(apps, schema_editor):
    del schema_editor
    ContentType, Permission = _permission_models(apps)

    target_content_type, _ = ContentType.objects.get_or_create(
        app_label="release",
        model="releasepermission",
    )
    source_content_type = ContentType.objects.filter(
        app_label="core",
        model="adminnotice",
    ).first()
    source_permission = None
    if source_content_type is not None:
        source_permission = Permission.objects.filter(
            content_type=source_content_type,
            codename=CODENAME,
        ).first()

    target_permission = Permission.objects.filter(
        content_type=target_content_type,
        codename=CODENAME,
    ).first()
    if source_permission is not None:
        if target_permission is not None and target_permission.pk != source_permission.pk:
            raise RuntimeError(
                "Cannot transfer upgrade-check permission because both the legacy "
                "core permission and the release permission already exist."
            )
        source_permission.content_type = target_content_type
        source_permission.name = PERMISSION_NAME
        source_permission.save(update_fields=["content_type", "name"])
        return

    Permission.objects.get_or_create(
        content_type=target_content_type,
        codename=CODENAME,
        defaults={"name": PERMISSION_NAME},
    )


def move_upgrade_permission_back_to_core(apps, schema_editor):
    del schema_editor
    ContentType, Permission = _permission_models(apps)

    source_content_type, _ = ContentType.objects.get_or_create(
        app_label="core",
        model="adminnotice",
    )
    target_content_type = ContentType.objects.filter(
        app_label="release",
        model="releasepermission",
    ).first()
    if target_content_type is None:
        return

    target_permission = Permission.objects.filter(
        content_type=target_content_type,
        codename=CODENAME,
    ).first()
    source_permission = Permission.objects.filter(
        content_type=source_content_type,
        codename=CODENAME,
    ).first()
    if target_permission is not None:
        if source_permission is not None and source_permission.pk != target_permission.pk:
            raise RuntimeError(
                "Cannot reverse upgrade-check permission ownership because both the "
                "release permission and legacy core permission exist."
            )
        target_permission.content_type = source_content_type
        target_permission.name = PERMISSION_NAME
        target_permission.save(update_fields=["content_type", "name"])

    if not Permission.objects.filter(content_type=target_content_type).exists():
        target_content_type.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0004_release_remaining_models"),
        ("release", "0002_alter_package_test_command"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReleasePermission",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "verbose_name": "Release Permission",
                "verbose_name_plural": "Release Permissions",
                "permissions": [
                    ("can_trigger_upgrade_checks", "Can trigger upgrade checks"),
                ],
                "default_permissions": (),
                "managed": False,
            },
        ),
        migrations.RunPython(
            move_upgrade_permission_to_release,
            move_upgrade_permission_back_to_core,
        ),
    ]
