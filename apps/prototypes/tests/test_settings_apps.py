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

