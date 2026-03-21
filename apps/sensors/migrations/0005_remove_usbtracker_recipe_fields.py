from django.db import migrations, models


PRESERVED_USB_TRACKER_COLUMNS = (
    ("recipe_id", "recipe_id__preserved"),
    ("cooldown_seconds", "cooldown_seconds__preserved"),
    ("last_match_signature", "last_match_signature__preserved"),
    ("last_recipe_result", "last_recipe_result__preserved"),
    ("last_triggered_at", "last_triggered_at__preserved"),
)


def _rename_usb_tracker_columns(apps, schema_editor, *, forward: bool) -> None:
    """Rename retired USB tracker columns so rollback can restore their data.

    Args:
        apps: Historical app registry supplied by Django migrations.
        schema_editor: Active schema editor for the current database.
        forward: When ``True``, move retired columns to preserved names; otherwise
            restore their original names.

    Returns:
        ``None``.
    """
    usb_tracker = apps.get_model("sensors", "UsbTracker")
    table = schema_editor.quote_name(usb_tracker._meta.db_table)

    for current_name, preserved_name in PRESERVED_USB_TRACKER_COLUMNS:
        source_name, target_name = (current_name, preserved_name) if forward else (preserved_name, current_name)
        source_column = schema_editor.quote_name(source_name)
        target_column = schema_editor.quote_name(target_name)
        schema_editor.execute(
            f"ALTER TABLE {table} RENAME COLUMN {source_column} TO {target_column}"
        )


def preserve_usb_tracker_columns(apps, schema_editor) -> None:
    """Preserve retired USB tracker columns for reversible rollback support.

    Args:
        apps: Historical app registry supplied by Django migrations.
        schema_editor: Active schema editor for the current database.

    Returns:
        ``None``.
    """
    _rename_usb_tracker_columns(apps, schema_editor, forward=True)


def restore_usb_tracker_columns(apps, schema_editor) -> None:
    """Restore preserved USB tracker columns during migration rollback.

    Args:
        apps: Historical app registry supplied by Django migrations.
        schema_editor: Active schema editor for the current database.

    Returns:
        ``None``.
    """
    _rename_usb_tracker_columns(apps, schema_editor, forward=False)


class Migration(migrations.Migration):

    dependencies = [
        ("recipes", "0001_initial"),
        ("sensors", "0004_usbtracker"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    preserve_usb_tracker_columns,
                    restore_usb_tracker_columns,
                ),
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="usbtracker",
                    name="recipe",
                ),
                migrations.RemoveField(
                    model_name="usbtracker",
                    name="cooldown_seconds",
                ),
                migrations.RemoveField(
                    model_name="usbtracker",
                    name="last_match_signature",
                ),
                migrations.RemoveField(
                    model_name="usbtracker",
                    name="last_recipe_result",
                ),
                migrations.RemoveField(
                    model_name="usbtracker",
                    name="last_triggered_at",
                ),
            ],
        ),
        migrations.AlterField(
            model_name="usbtracker",
            name="required_file_regex",
            field=models.TextField(
                blank=True,
                help_text="Optional regex used to validate file contents before marking a match.",
            ),
        ),
    ]
