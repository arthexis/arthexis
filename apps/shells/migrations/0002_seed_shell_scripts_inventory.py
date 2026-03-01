"""Seed shell script inventory records from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "shells__shell_scripts_inventory.json"
)


def _load_fixture(path: Path) -> list[dict]:
    """Return parsed fixture payload or an empty list when unavailable."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    return payload if isinstance(payload, list) else []


def seed_shell_scripts(apps, schema_editor):
    """Create or update shell script records described in the fixture."""

    del schema_editor

    Application = apps.get_model("app", "Application")
    ShellScript = apps.get_model("shells", "ShellScript")

    application_manager = getattr(Application, "all_objects", Application._base_manager)
    shell_manager = getattr(ShellScript, "all_objects", ShellScript._base_manager)

    for entry in _load_fixture(FIXTURE_PATH):
        if not isinstance(entry, dict) or entry.get("model") != "shells.shellscript":
            continue

        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue

        path = str(fields.get("path", "")).strip()
        kind = str(fields.get("kind", "")).strip()
        if not path or kind not in {"base", "app"}:
            continue

        managed_by = None
        managed_by_value = fields.get("managed_by")
        if isinstance(managed_by_value, (list, tuple)) and managed_by_value:
            app_name = str(managed_by_value[0]).strip()
            if app_name:
                managed_by, _ = application_manager.get_or_create(name=app_name)

        shell_manager.update_or_create(
            path=path,
            defaults={
                "name": str(fields.get("name") or Path(path).name),
                "kind": kind,
                "managed_by": managed_by,
                "is_seed_data": bool(fields.get("is_seed_data", True)),
                "is_user_data": bool(fields.get("is_user_data", False)),
                "is_deleted": bool(fields.get("is_deleted", False)),
            },
        )


def unseed_shell_scripts(apps, schema_editor):
    """Delete shell script records defined by the fixture."""

    del schema_editor
    ShellScript = apps.get_model("shells", "ShellScript")
    shell_manager = getattr(ShellScript, "all_objects", ShellScript._base_manager)

    paths = []
    for entry in _load_fixture(FIXTURE_PATH):
        if not isinstance(entry, dict) or entry.get("model") != "shells.shellscript":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue
        path = str(fields.get("path", "")).strip()
        if path:
            paths.append(path)

    if paths:
        shell_manager.filter(path__in=paths).delete()


class Migration(migrations.Migration):
    """Seed shell script inventory data from fixtures."""

    dependencies = [
        ("shells", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_shell_scripts, unseed_shell_scripts),
    ]
