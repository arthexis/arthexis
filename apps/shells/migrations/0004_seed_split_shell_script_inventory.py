"""Seed base and app shell script inventory records from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations

BASE_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "shells__base_shell_scripts_inventory.json"
)
APP_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "shells__app_shell_scripts_inventory.json"
)


def _load_fixture(path: Path) -> list[dict]:
    """Return parsed fixture payload or an empty list when unavailable."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    return payload if isinstance(payload, list) else []


def seed_shell_scripts(apps, schema_editor):
    """Create or update shell script records described in split fixtures."""

    del schema_editor

    Application = apps.get_model("app", "Application")
    BaseShellScript = apps.get_model("shells", "BaseShellScript")
    AppShellScript = apps.get_model("shells", "AppShellScript")

    application_manager = getattr(Application, "all_objects", Application._base_manager)
    base_manager = getattr(BaseShellScript, "all_objects", BaseShellScript._base_manager)
    app_manager = getattr(AppShellScript, "all_objects", AppShellScript._base_manager)

    for entry in _load_fixture(BASE_FIXTURE_PATH):
        if not isinstance(entry, dict) or entry.get("model") != "shells.baseshellscript":
            continue

        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue

        path = str(fields.get("path", "")).strip()
        if not path:
            continue

        base_manager.update_or_create(
            path=path,
            defaults={
                "name": str(fields.get("name") or Path(path).name),
                "is_seed_data": bool(fields.get("is_seed_data", True)),
                "is_user_data": bool(fields.get("is_user_data", False)),
                "is_deleted": bool(fields.get("is_deleted", False)),
            },
        )

    for entry in _load_fixture(APP_FIXTURE_PATH):
        if not isinstance(entry, dict) or entry.get("model") != "shells.appshellscript":
            continue

        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue

        path = str(fields.get("path", "")).strip()
        if not path:
            continue

        managed_by_value = fields.get("managed_by")
        if not isinstance(managed_by_value, (list, tuple)) or not managed_by_value:
            continue

        app_name = str(managed_by_value[0]).strip()
        if not app_name:
            continue

        managed_by, _ = application_manager.get_or_create(name=app_name)
        app_manager.update_or_create(
            path=path,
            defaults={
                "name": str(fields.get("name") or Path(path).name),
                "managed_by": managed_by,
                "is_seed_data": bool(fields.get("is_seed_data", True)),
                "is_user_data": bool(fields.get("is_user_data", False)),
                "is_deleted": bool(fields.get("is_deleted", False)),
            },
        )


def unseed_shell_scripts(apps, schema_editor):
    """Delete shell script records defined by split fixtures."""

    del schema_editor

    BaseShellScript = apps.get_model("shells", "BaseShellScript")
    AppShellScript = apps.get_model("shells", "AppShellScript")
    base_manager = getattr(BaseShellScript, "all_objects", BaseShellScript._base_manager)
    app_manager = getattr(AppShellScript, "all_objects", AppShellScript._base_manager)

    base_paths = []
    for entry in _load_fixture(BASE_FIXTURE_PATH):
        if not isinstance(entry, dict) or entry.get("model") != "shells.baseshellscript":
            continue
        fields = entry.get("fields")
        if isinstance(fields, dict):
            path = str(fields.get("path", "")).strip()
            if path:
                base_paths.append(path)

    app_paths = []
    for entry in _load_fixture(APP_FIXTURE_PATH):
        if not isinstance(entry, dict) or entry.get("model") != "shells.appshellscript":
            continue
        fields = entry.get("fields")
        if isinstance(fields, dict):
            path = str(fields.get("path", "")).strip()
            if path:
                app_paths.append(path)

    if base_paths:
        base_manager.filter(path__in=base_paths).delete()
    if app_paths:
        app_manager.filter(path__in=app_paths).delete()


class Migration(migrations.Migration):
    """Seed split shell script inventory data from fixtures."""

    dependencies = [
        ("shells", "0003_baseshellscript_appshellscript_delete_shellscript"),
    ]

    operations = [
        migrations.RunPython(seed_shell_scripts, unseed_shell_scripts),
    ]
