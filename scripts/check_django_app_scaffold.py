"""Validate Django app scaffold conventions under ``apps/``."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.settings.base import APPS_DIR
from config.settings.apps import NON_DJANGO_UTILITY_PACKAGES, _to_module_path

REQUIRED_TOP_LEVEL_APP_FILES = ("__init__.py", "apps.py")
REQUIRED_TOP_LEVEL_APP_DIRS = ("migrations/__init__.py",)


def _is_top_level_app(path: Path) -> bool:
    """Return whether ``path`` points to a direct child package under ``apps/``."""

    relative = path.relative_to(APPS_DIR)
    return len(relative.parts) == 1 and not _is_private_package_path(relative)


def _is_private_package_path(path: Path) -> bool:
    """Return whether any path segment is private or hidden."""

    return any(part.startswith((".", "_")) for part in path.parts)


def _iter_top_level_app_dirs() -> list[Path]:
    """Return sorted top-level app directories under ``apps/`` excluding utility packages."""

    app_dirs: list[Path] = []
    for candidate in sorted(APPS_DIR.iterdir()):
        if not candidate.is_dir():
            continue
        if not _is_top_level_app(candidate):
            continue

        module_path = _to_module_path(candidate)
        if module_path in NON_DJANGO_UTILITY_PACKAGES:
            continue

        app_dirs.append(candidate)

    return app_dirs


def collect_missing_scaffold_paths() -> dict[str, list[str]]:
    """Collect missing scaffold paths for intended top-level Django apps."""

    missing: dict[str, list[str]] = {}
    for app_dir in _iter_top_level_app_dirs():
        module_path = _to_module_path(app_dir)
        missing_items: list[str] = []

        for relative_path in REQUIRED_TOP_LEVEL_APP_FILES + REQUIRED_TOP_LEVEL_APP_DIRS:
            if not (app_dir / relative_path).exists():
                missing_items.append(relative_path)

        if missing_items:
            missing[module_path] = missing_items

    return missing


def main() -> int:
    """Run scaffold checks and return a process exit code."""

    missing = collect_missing_scaffold_paths()
    if not missing:
        print("All intended top-level Django apps include required scaffold files.")
        return 0

    print("Missing Django app scaffold files detected:")
    for module_path, items in sorted(missing.items()):
        print(f"- {module_path}")
        for item in items:
            print(f"  - {item}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
