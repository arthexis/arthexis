from django.db import migrations


def rename_usbtracker_recipe_to_archive(apps, schema_editor):
    """Rename the USB tracker recipe column to preserve values for rollback."""

    schema_editor.execute("ALTER TABLE sensors_usbtracker RENAME COLUMN recipe_id TO archived_recipe_id")


def restore_usbtracker_recipe_column(apps, schema_editor):
    """Restore the USB tracker recipe column during rollback."""

    schema_editor.execute("ALTER TABLE sensors_usbtracker RENAME COLUMN archived_recipe_id TO recipe_id")


class Migration(migrations.Migration):

    dependencies = [
        ("sensors", "0004_usbtracker"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_usbtracker_recipe_to_archive,
                    restore_usbtracker_recipe_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="usbtracker",
                    name="recipe",
                )
            ],
        ),
    ]
