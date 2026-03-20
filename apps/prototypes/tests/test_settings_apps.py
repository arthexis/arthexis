from __future__ import annotations

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


def test_hidden_prototype_apps_require_explicit_activation(monkeypatch, tmp_path):
    apps_root = tmp_path / "apps"
    _write_app(apps_root, ("blog",))
    _write_app(apps_root, ("_legacy", "fitbit_migration_only"))
    _write_app(apps_root, ("_prototypes", "vision_lab"))
    monkeypatch.setattr(settings_apps, "APPS_DIR", apps_root)

    discovered = settings_apps._load_local_apps()

    assert "apps.blog" in discovered
    assert "apps._legacy.fitbit_migration_only" not in discovered
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


def test_top_level_app_with_empty_migrations_dir_is_not_discovered(
    monkeypatch, tmp_path
):
    apps_root = tmp_path / "apps"
    fitbit_dir = apps_root / "fitbit"
    fitbit_dir.mkdir(parents=True, exist_ok=True)
    (apps_root / "__init__.py").write_text('"""test package."""\n', encoding="utf-8")
    (fitbit_dir / "__init__.py").write_text(
        '"""compatibility package."""\n',
        encoding="utf-8",
    )
    (fitbit_dir / "migrations").mkdir()

    monkeypatch.setattr(settings_apps, "APPS_DIR", apps_root)

    assert "apps.fitbit" not in settings_apps._load_local_apps()
