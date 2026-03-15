from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.pr_origin(6232)

REQUIRED_MODULES = ("functions", "models", "templates", "triggers", "views")
MODULES_WITH_BODIES = {"functions", "models", "triggers", "views"}


def _ada_apps_root() -> Path:
    return Path("ada/apps")


def _discover_ada_apps() -> list[str]:
    apps_root = _ada_apps_root()
    return sorted(
        directory.name
        for directory in apps_root.iterdir()
        if directory.is_dir() and (directory / "models").is_dir()
    )


@pytest.mark.parametrize("app_name", _discover_ada_apps())
def test_ada_app_scaffold_has_required_layout_and_parent_units(app_name: str) -> None:
    apps_root = _ada_apps_root()

    assert (apps_root / f"apps-{app_name}.ads").exists()

    for module_name in REQUIRED_MODULES:
        module_dir = apps_root / app_name / module_name
        leaf_basename = f"{app_name}_{module_name}"

        assert module_dir.is_dir()
        assert (module_dir / f"{leaf_basename}.ads").exists()

        if module_name in MODULES_WITH_BODIES:
            assert (module_dir / f"{leaf_basename}.adb").exists()

        assert (apps_root / f"apps-{app_name}-{module_name}.ads").exists()


@pytest.mark.parametrize("app_name", _discover_ada_apps())
def test_ada_app_scaffold_files_are_mapped_in_gpr_naming(app_name: str) -> None:
    gpr_text = Path("ada/arthexis_ada.gpr").read_text(encoding="utf-8")

    assert "package Naming is" in gpr_text

    for module_name in REQUIRED_MODULES:
        spec_relpath = f"apps/{app_name}/{module_name}/{app_name}_{module_name}.ads"
        assert spec_relpath in gpr_text

        if module_name in MODULES_WITH_BODIES:
            body_relpath = f"apps/{app_name}/{module_name}/{app_name}_{module_name}.adb"
            assert body_relpath in gpr_text
