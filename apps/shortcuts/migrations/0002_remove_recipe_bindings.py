from django.db import migrations


def rename_shortcut_recipe_to_archive(apps, schema_editor):
    """Rename the shortcut recipe column to preserve values for rollback."""

    schema_editor.execute("ALTER TABLE shortcuts_shortcut RENAME COLUMN recipe_id TO archived_recipe_id")


def restore_shortcut_recipe_column(apps, schema_editor):
    """Restore the shortcut recipe column during rollback."""

    schema_editor.execute("ALTER TABLE shortcuts_shortcut RENAME COLUMN archived_recipe_id TO recipe_id")


def rename_clipboard_pattern_recipe_to_archive(apps, schema_editor):
    """Rename the clipboard pattern recipe column to preserve values for rollback."""

    schema_editor.execute("ALTER TABLE shortcuts_clipboardpattern RENAME COLUMN recipe_id TO archived_recipe_id")


def restore_clipboard_pattern_recipe_column(apps, schema_editor):
    """Restore the clipboard pattern recipe column during rollback."""

    schema_editor.execute("ALTER TABLE shortcuts_clipboardpattern RENAME COLUMN archived_recipe_id TO recipe_id")


class Migration(migrations.Migration):

    dependencies = [
        ("shortcuts", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_shortcut_recipe_to_archive,
                    restore_shortcut_recipe_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="shortcut",
                    name="recipe",
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_clipboard_pattern_recipe_to_archive,
                    restore_clipboard_pattern_recipe_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="clipboardpattern",
                    name="recipe",
                )
            ],
        ),
    ]
