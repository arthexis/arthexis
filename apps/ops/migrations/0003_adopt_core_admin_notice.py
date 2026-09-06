import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


MODEL_NAME = "adminnotice"


def _move_content_type(apps, source_label: str, target_label: str) -> None:
    ContentType = apps.get_model("contenttypes", "ContentType")
    source = ContentType.objects.filter(
        app_label=source_label,
        model=MODEL_NAME,
    ).first()
    if source is None:
        return

    target = ContentType.objects.filter(
        app_label=target_label,
        model=MODEL_NAME,
    ).first()
    if target is not None and target.pk != source.pk:
        raise RuntimeError(
            "Cannot transfer AdminNotice ContentType ownership because both "
            f"{source_label}.{MODEL_NAME} and {target_label}.{MODEL_NAME} exist."
        )

    source.app_label = target_label
    source.save(update_fields=["app_label"])


def move_core_content_type_to_ops(apps, schema_editor):
    del schema_editor
    _move_content_type(apps, "core", "ops")


def move_ops_content_type_back_to_core(apps, schema_editor):
    del schema_editor
    _move_content_type(apps, "ops", "core")


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0004_release_remaining_models"),
        ("ops", "0002_initial"),
        ("release", "0003_move_upgrade_permission"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    move_core_content_type_to_ops,
                    move_ops_content_type_back_to_core,
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="AdminNotice",
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
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("message", models.TextField()),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("dismissed_at", models.DateTimeField(blank=True, null=True)),
                        (
                            "dismissed_by",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="dismissed_admin_notices",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "core_adminnotice",
                        "ordering": ["-created_at"],
                        "verbose_name": "Admin Notice",
                        "verbose_name_plural": "Admin Notices",
                    },
                ),
            ],
        ),
    ]
