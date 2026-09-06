import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


MODEL_NAME = "usageevent"


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
            "Cannot transfer UsageEvent ContentType ownership because both "
            f"{source_label}.{MODEL_NAME} and {target_label}.{MODEL_NAME} exist."
        )

    source.app_label = target_label
    source.save(update_fields=["app_label"])


def move_core_content_type_to_analytics(apps, schema_editor):
    del schema_editor
    _move_content_type(apps, "core", "analytics")


def move_analytics_content_type_back_to_core(apps, schema_editor):
    del schema_editor
    _move_content_type(apps, "analytics", "core")


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0004_release_remaining_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    move_core_content_type_to_analytics,
                    move_analytics_content_type_back_to_core,
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="UsageEvent",
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
                        (
                            "timestamp",
                            models.DateTimeField(
                                db_index=True,
                                default=django.utils.timezone.now,
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="usage_events",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        ("app_label", models.CharField(db_index=True, max_length=100)),
                        ("view_name", models.CharField(db_index=True, max_length=255)),
                        ("path", models.TextField()),
                        ("method", models.CharField(max_length=10)),
                        ("status_code", models.PositiveIntegerField()),
                        (
                            "model_label",
                            models.CharField(blank=True, default="", max_length=255),
                        ),
                        (
                            "action",
                            models.CharField(
                                choices=[
                                    ("read", "Read"),
                                    ("create", "Create"),
                                    ("update", "Update"),
                                    ("delete", "Delete"),
                                ],
                                db_index=True,
                                default="read",
                                max_length=12,
                            ),
                        ),
                        ("metadata", models.JSONField(blank=True, default=dict)),
                    ],
                    options={
                        "db_table": "core_usageevent",
                        "ordering": ["-timestamp"],
                        "indexes": [
                            models.Index(
                                fields=["app_label", "view_name", "timestamp"],
                                name="core_usagee_app_lab_e90cdc_idx",
                            )
                        ],
                    },
                ),
            ],
        ),
    ]
