from __future__ import annotations

import importlib
from pathlib import Path

from config.settings import apps as settings_apps


def _write_app(apps_root: Path, relative_parts: tuple[str, ...]) -> None:
    app_dir = apps_root.joinpath(*relative_parts)
    app_dir.mkdir(parents=True, exist_ok=True)
    for package_dir in [apps_root, *[apps_root.joinpath(*relative_parts[:index]) for index in range(1, len(relative_parts) + 1)]]:
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


def test_hidden_prototype_apps_require_explicit_activation(monkeypatch, tmp_path):
    apps_root = tmp_path / "apps"
    _write_app(apps_root, ("blog",))
    _write_app(apps_root, ("_prototypes", "vision_lab"))
    monkeypatch.setattr(settings_apps, "APPS_DIR", apps_root)

    discovered = settings_apps._load_local_apps()

    assert "apps.blog" in discovered
    assert "apps._prototypes.vision_lab" not in discovered

    monkeypatch.setenv("ARTHEXIS_PROTOTYPE_APP", "apps._prototypes.vision_lab")
    monkeypatch.setattr(
        settings_apps.importlib.util,
        "find_spec",
        lambda module_name: object()
        if module_name == "apps._prototypes.vision_lab"
        else None,
    )

    assert settings_apps._load_active_prototype_app() == ["apps._prototypes.vision_lab"]


def test_camera_utility_package_stays_out_of_local_django_app_discovery(monkeypatch, tmp_path):
    apps_root = tmp_path / "apps"
    camera_dir = apps_root / "camera"
    camera_dir.mkdir(parents=True, exist_ok=True)
    (apps_root / "__init__.py").write_text('"""test package."""\n', encoding="utf-8")
    (camera_dir / "__init__.py").write_text('"""camera shim package."""\n', encoding="utf-8")

    monkeypatch.setattr(settings_apps, "APPS_DIR", apps_root)

    assert "apps.camera" not in settings_apps._load_local_apps()


def test_legacy_camera_shim_remains_importable_for_prototype_integrations():
    camera_module = importlib.import_module("apps.camera")
    rpi_module = importlib.import_module("apps.camera.rpi")
    rfid_module = importlib.import_module("apps.camera.rfid")

    assert camera_module.capture_rpi_snapshot is rpi_module.capture_rpi_snapshot
    assert rfid_module.queue_camera_snapshot is not None
