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


def test_legacy_camera_shim_remains_importable_for_prototype_integrations():
    camera_module = importlib.import_module("apps.camera")
    rpi_module = importlib.import_module("apps.camera.rpi")
    rfid_module = importlib.import_module("apps.camera.rfid")

    assert camera_module.capture_rpi_snapshot is rpi_module.capture_rpi_snapshot
    assert rfid_module.queue_camera_snapshot is not None


def test_removed_runtime_apps_only_remain_available_through_explicit_legacy_shims():
    assert "apps.extensions" not in settings_apps.LOCAL_APPS
    assert "apps.prompts" not in settings_apps.LOCAL_APPS
    assert "apps.selenium" not in settings_apps.LOCAL_APPS
    assert "apps.socials" not in settings_apps.LOCAL_APPS
    assert "apps.sponsors" not in settings_apps.LOCAL_APPS
    assert "apps.survey" not in settings_apps.LOCAL_APPS
    assert (
        "apps._legacy.prompts_migration_only.apps.PromptsMigrationOnlyConfig"
        in settings_apps.LEGACY_MIGRATION_APPS
    )
    assert (
        "apps._legacy.selenium_migration_only.apps.SeleniumMigrationOnlyConfig"
        in settings_apps.LEGACY_MIGRATION_APPS
    )
    assert settings_apps.MIGRATION_MODULES["selenium"] == "apps.selenium.migrations"
    assert (
        settings_apps.MIGRATION_MODULES["socials"]
        == "apps._legacy.socials_migration_only.migrations"
    )
    assert (
        settings_apps.MIGRATION_MODULES["sponsors"]
        == "apps._legacy.sponsors_migration_only.migrations"
    )
    assert (
        "apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig"
        in settings_apps.LEGACY_MIGRATION_APPS
    )
    assert (
        "apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig"
        in settings_apps.LEGACY_MIGRATION_APPS
    )
    assert (
        "apps._legacy.survey_migration_only.apps.SurveyMigrationOnlyConfig"
        in settings_apps.LEGACY_MIGRATION_APPS
    )


def test_legacy_migration_apps_are_kept_sorted_for_maintainability():
    assert settings_apps.LEGACY_MIGRATION_APPS == sorted(
        settings_apps.LEGACY_MIGRATION_APPS
    )


def test_legacy_runtime_packages_are_derived_from_legacy_migration_apps():
    assert settings_apps._legacy_runtime_app_packages() == {
        "apps.extensions",
        "apps.prompts",
        "apps.selenium",
        "apps.socials",
        "apps.sponsors",
        "apps.survey",
    }


def test_archived_socials_and_sponsors_runtime_surfaces_are_removed():
    legacy_socials_migrations = {
        path.name
        for path in Path("apps/_legacy/socials_migration_only/migrations").iterdir()
        if path.is_file()
    }
    sponsors_files = {path.name for path in Path("apps/sponsors").iterdir() if path.is_file()}

    assert not Path("apps/socials").exists()
    assert legacy_socials_migrations == {
        "__init__.py",
        "0001_initial.py",
        "0002_initial.py",
        "0003_alter_blueskyprofile_group_alter_blueskyprofile_user_and_more.py",
        "0004_remove_blueskyprofile_discordprofile.py",
    }
    assert sponsors_files == {"__init__.py"}
