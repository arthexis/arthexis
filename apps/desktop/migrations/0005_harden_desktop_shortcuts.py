from __future__ import annotations

from django.db import migrations, models


_ARCHIVE_KEY = "retired_command_mode_review"
_REVIEW_URL = "http://127.0.0.1:{port}/admin/desktop/desktopshortcut/"


def _archive_command_shortcuts(apps, schema_editor):
    """Normalize command-based shortcuts into disabled URL records for review."""

    DesktopShortcut = apps.get_model("desktop", "DesktopShortcut")

    for shortcut in DesktopShortcut.objects.all():
        has_retired_behavior = (
            shortcut.launch_mode == "command"
            or bool((shortcut.command or "").strip())
            or bool((shortcut.condition_command or "").strip())
        )
        if not has_retired_behavior:
            continue

        extra_entries = dict(shortcut.extra_entries or {})
        if _ARCHIVE_KEY in extra_entries:
            continue

        extra_entries[_ARCHIVE_KEY] = {
            "command": shortcut.command,
            "condition_command": shortcut.condition_command,
            "launch_mode": shortcut.launch_mode,
            "target_url": shortcut.target_url,
            "was_enabled": shortcut.is_enabled,
        }
        shortcut.extra_entries = extra_entries
        shortcut.launch_mode = "url"
        shortcut.target_url = (shortcut.target_url or "").strip() or _REVIEW_URL
        shortcut.is_enabled = False
        shortcut.command = ""
        shortcut.condition_command = ""
        shortcut.save(
            update_fields=[
                "extra_entries",
                "is_enabled",
                "launch_mode",
                "target_url",
                "command",
                "condition_command",
            ]
        )


def _restore_archived_command_shortcuts(apps, schema_editor):
    """Restore archived command shortcut values when reversing the migration."""

    DesktopShortcut = apps.get_model("desktop", "DesktopShortcut")

    for shortcut in DesktopShortcut.objects.all():
        extra_entries = dict(shortcut.extra_entries or {})
        archived = extra_entries.pop(_ARCHIVE_KEY, None)
        if not archived:
            continue

        shortcut.extra_entries = extra_entries
        shortcut.command = archived.get("command", "")
        shortcut.condition_command = archived.get("condition_command", "")
        shortcut.launch_mode = archived.get("launch_mode", "url")
        shortcut.target_url = archived.get("target_url", shortcut.target_url)
        shortcut.is_enabled = archived.get("was_enabled", shortcut.is_enabled)
        shortcut.save(
            update_fields=[
                "extra_entries",
                "is_enabled",
                "launch_mode",
                "target_url",
                "command",
                "condition_command",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("desktop", "0004_desktopshortcut_install_location"),
    ]

    operations = [
        migrations.RunPython(
            _archive_command_shortcuts,
            _restore_archived_command_shortcuts,
        ),
        migrations.RemoveField(
            model_name="desktopshortcut",
            name="command",
        ),
        migrations.RemoveField(
            model_name="desktopshortcut",
            name="condition_command",
        ),
        migrations.AlterField(
            model_name="desktopshortcut",
            name="condition_expression",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Optional constrained boolean expression evaluated against "
                    "context keys: has_desktop_ui, has_feature, is_staff, "
                    "is_superuser, group_names."
                ),
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="desktopshortcut",
            name="launch_mode",
            field=models.CharField(
                choices=[("url", "URL")],
                default="url",
                help_text="Desktop shortcuts always open a URL through the browser helper.",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="desktopshortcut",
            name="target_url",
            field=models.CharField(
                blank=True,
                help_text="HTTP or HTTPS URL to open. Supports the {port} placeholder.",
                max_length=512,
            ),
        ),
    ]
