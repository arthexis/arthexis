from __future__ import annotations

import importlib
from pathlib import Path

from config.settings import apps as settings_apps

def _write_app(apps_root: Path, relative_parts: tuple[str, ...]) -> None:
    app_dir = apps_root.joinpath(*relative_parts)
    app_dir.mkdir(parents=True, exist_ok=True)
    package_dirs = [apps_root] + [
        apps_root.joinpath(*relative_parts[:index])
        for index in range(1, len(relative_parts) + 1)
    ]
    for package_dir in package_dirs:
        package_dir.mkdir(parents=True, exist_ok=True)
        init_path = package_dir / "__init__.py"
        if not init_path.exists():
            init_path.write_text('"""test package."""\n', encoding="utf-8")
    (app_dir / "apps.py").write_text(
        "from django.apps import AppConfig\n\n\n"
        "class TestConfig(AppConfig):\n"
        '    default_auto_field = "django.db.models.BigAutoField"\n'
        f'    name = "apps.{".".join(relative_parts)}"\n',
        encoding="utf-8",
    )

def test_hidden_packages_stay_out_of_local_django_app_discovery(monkeypatch, tmp_path):
    apps_root = tmp_path / "apps"
    _write_app(apps_root, ("public_updates",))
    _write_app(apps_root, ("_prototypes", "vision_lab"))
    monkeypatch.setattr(settings_apps, "APPS_DIR", apps_root)

    discovered = settings_apps._load_local_apps()

    assert "apps.public_updates" in discovered
    assert "apps._prototypes.vision_lab" not in discovered

def test_camera_utility_package_stays_out_of_local_django_app_discovery(
    monkeypatch, tmp_path
):
    apps_root = tmp_path / "apps"
    camera_dir = apps_root / "camera"
    camera_dir.mkdir(parents=True, exist_ok=True)
    (apps_root / "__init__.py").write_text('"""test package."""\n', encoding="utf-8")
    (camera_dir / "__init__.py").write_text(
        '"""camera shim package."""\n', encoding="utf-8"
    )

    monkeypatch.setattr(settings_apps, "APPS_DIR", apps_root)

    assert "apps.camera" not in settings_apps._load_local_apps()

def test_archived_socials_and_sponsors_runtime_surfaces_are_removed():
    socials_files = {path.name for path in Path("apps/socials").iterdir() if path.is_file()}

    assert not Path("apps/selenium").exists()
    assert socials_files == {"__init__.py"}
    assert not Path("apps/sponsors").exists()


def test_import_base_module_uses_module_path_for_project_local_entries(monkeypatch):
    imported_modules: list[str] = []

    monkeypatch.setattr(
        settings_apps,
        "import_module",
        lambda module_name: imported_modules.append(module_name),
    )

    settings_apps._import_base_module("apps.actions")

    assert imported_modules == ["apps.actions"]


def test_import_base_module_supports_nested_app_config_paths(monkeypatch):
    imported_modules: list[str] = []

    monkeypatch.setattr(
        settings_apps,
        "import_module",
        lambda module_name: imported_modules.append(module_name),
    )

    settings_apps._import_base_module("apps.celery.beat_app.CeleryBeatConfig")

    assert imported_modules == ["apps.celery.beat_app"]
