from django.db import migrations


BACKUP_TABLE = "emails_emailcollector_notification_recipe_backup"
BATCH_SIZE = 500


CREATE_BACKUP_TABLE_SQL = f"""
CREATE TABLE {BACKUP_TABLE} (
    collector_id bigint PRIMARY KEY,
    recipe_id bigint NOT NULL
)
"""


DROP_BACKUP_TABLE_SQL = f"DROP TABLE {BACKUP_TABLE}"


def _flush_backup_rows(cursor, rows):
    """Insert a batch of collector-to-recipe links into the backup table.

    Args:
        cursor: Database cursor used for the migration.
        rows: Sequence of ``(collector_id, recipe_id)`` tuples to persist.

    Returns:
        None.
    """
    if not rows:
        return

    cursor.executemany(
        f"INSERT INTO {BACKUP_TABLE} (collector_id, recipe_id) VALUES (%s, %s)",
        rows,
    )


def backup_notification_recipe_links(apps, schema_editor):
    """Persist recipe links before removing the collector field.

    Args:
        apps: Historical app registry for this migration state.
        schema_editor: Active schema editor for the migration run.

    Returns:
        None.
    """
    EmailCollector = apps.get_model("emails", "EmailCollector")
    collectors_with_recipe = EmailCollector.objects.filter(
        notification_recipe__isnull=False
    ).order_by("pk")
    if not collectors_with_recipe.exists():
        return

    with schema_editor.connection.cursor() as cursor:
        batch = []
        for collector_id, recipe_id in collectors_with_recipe.values_list(
            "pk", "notification_recipe_id"
        ).iterator(chunk_size=BATCH_SIZE):
            batch.append((collector_id, recipe_id))
            if len(batch) >= BATCH_SIZE:
                _flush_backup_rows(cursor, batch)
                batch = []
        _flush_backup_rows(cursor, batch)


def restore_notification_recipe_links(apps, schema_editor):
    """Restore preserved recipe links when this migration is reversed.

    Args:
        apps: Historical app registry for this migration state.
        schema_editor: Active schema editor for the migration run.

    Returns:
        None.
    """
    EmailCollector = apps.get_model("emails", "EmailCollector")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"SELECT collector_id, recipe_id FROM {BACKUP_TABLE} ORDER BY collector_id"
        )
        backup_rows = cursor.fetchall()

    if not backup_rows:
        return

    for collector_id, recipe_id in backup_rows:
        EmailCollector.objects.filter(pk=collector_id).update(notification_recipe_id=recipe_id)


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0011_merge_20260310_1506"),
        ("recipes", "0003_recipeproduct"),
    ]

    operations = [
        migrations.RunSQL(CREATE_BACKUP_TABLE_SQL, DROP_BACKUP_TABLE_SQL),
        migrations.RunPython(
            backup_notification_recipe_links,
            restore_notification_recipe_links,
        ),
        migrations.RemoveField(
            model_name="emailcollector",
            name="notification_recipe",
        ),
    ]
