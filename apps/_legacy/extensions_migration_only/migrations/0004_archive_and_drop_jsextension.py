"""Archive hosted JS extensions and remove the runtime table."""

from __future__ import annotations

from django.core.management.color import no_style
from django.db import migrations, models


ARCHIVE_FIELDS = (
    "is_seed_data",
    "is_user_data",
    "is_deleted",
    "slug",
    "name",
    "description",
    "version",
    "manifest_version",
    "is_enabled",
    "matches",
    "content_script",
    "background_script",
    "options_page",
    "permissions",
    "host_permissions",
)


def reset_sequences(models_to_reset, schema_editor):
    """Reset database sequences for restored models after explicit PK inserts."""

    sql_statements = schema_editor.connection.ops.sequence_reset_sql(
        no_style(), models_to_reset
    )
    if sql_statements:
        with schema_editor.connection.cursor() as cursor:
            for statement in sql_statements:
                cursor.execute(statement)


def archive_extensions(apps, schema_editor):
    """Copy hosted JS extension rows into the archival table before deletion."""

    ArchivedJsExtension = apps.get_model("extensions", "ArchivedJsExtension")
    JsExtension = apps.get_model("extensions", "JsExtension")

    to_archive = [
        ArchivedJsExtension(
            original_id=extension.pk,
            **{field: getattr(extension, field) for field in ARCHIVE_FIELDS},
        )
        for extension in JsExtension.objects.all().iterator()
    ]
    if to_archive:
        ArchivedJsExtension.objects.bulk_create(
            to_archive,
            update_conflicts=True,
            unique_fields=["original_id"],
            update_fields=list(ARCHIVE_FIELDS),
        )


def restore_extensions(apps, schema_editor):
    """Restore archived hosted JS extension rows when the migration is reversed."""

    ArchivedJsExtension = apps.get_model("extensions", "ArchivedJsExtension")
    JsExtension = apps.get_model("extensions", "JsExtension")

    to_restore = [
        JsExtension(
            id=archived.original_id,
            **{field: getattr(archived, field) for field in ARCHIVE_FIELDS},
        )
        for archived in ArchivedJsExtension.objects.all().iterator()
    ]
    if to_restore:
        JsExtension.objects.bulk_create(
            to_restore,
            update_conflicts=True,
            unique_fields=["id"],
            update_fields=list(ARCHIVE_FIELDS),
        )
    reset_sequences([JsExtension], schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("extensions", "0003_seed_github_resolve_comments_extension"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArchivedJsExtension",
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
                ("original_id", models.BigIntegerField(unique=True)),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("slug", models.SlugField(max_length=100)),
                ("name", models.CharField(max_length=150)),
                ("description", models.TextField(blank=True)),
                ("version", models.CharField(default="0.1.0", max_length=50)),
                ("manifest_version", models.PositiveSmallIntegerField(default=3)),
                ("is_enabled", models.BooleanField(default=True)),
                ("matches", models.TextField(blank=True)),
                ("content_script", models.TextField(blank=True)),
                ("background_script", models.TextField(blank=True)),
                ("options_page", models.TextField(blank=True)),
                ("permissions", models.TextField(blank=True)),
                ("host_permissions", models.TextField(blank=True)),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["slug", "original_id"]},
        ),
        migrations.RunPython(archive_extensions, restore_extensions),
        migrations.DeleteModel(name="JsExtension"),
    ]
