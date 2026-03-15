"""Static assurance checks for Ada app scaffolding layout and registration."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.pr("6232", "2026-03-15T00:00:00Z")
@pytest.mark.pr_origin(6232)
def test_ada_apps_have_expected_module_layout() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    apps_root = repo_root / "ada" / "apps"

    expected_apps = {"core", "ocpp"}
    expected_sections = {"models", "views", "templates", "functions", "triggers"}

    app_dirs = {path.name for path in apps_root.iterdir() if path.is_dir()}
    assert expected_apps.issubset(app_dirs)

    for app in sorted(expected_apps):
        app_path = apps_root / app
        section_dirs = {path.name for path in app_path.iterdir() if path.is_dir()}
        assert expected_sections == section_dirs


@pytest.mark.pr("6232", "2026-03-15T00:00:00Z")
@pytest.mark.pr_origin(6232)
def test_ada_app_units_are_registered_from_install_all() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    install_all = (repo_root / "ada" / "src" / "arthexis-apps.adb").read_text(encoding="utf-8")

    expected_installs = [
        "Apps.Core.Models.Core_Models.Install (Conn);",
        "Apps.Core.Functions.Core_Functions.Install (Conn);",
        "Apps.Core.Triggers.Core_Triggers.Install (Conn);",
        "Apps.OCPP.Models.OCPP_Models.Install (Conn);",
        "Apps.OCPP.Functions.OCPP_Functions.Install (Conn);",
        "Apps.OCPP.Triggers.OCPP_Triggers.Install (Conn);",
    ]

    for install_call in expected_installs:
        assert install_call in install_all


@pytest.mark.pr("6232", "2026-03-15T00:00:00Z")
@pytest.mark.pr_origin(6232)
def test_ada_gpr_maps_nonstandard_app_unit_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    gpr_text = (repo_root / "ada" / "arthexis_ada.gpr").read_text(encoding="utf-8")

    expected_gpr_entries = [
        'for Spec ("Apps.Core.Models.Core_Models") use',
        '"apps/core/models/core_models.ads";',
        'for Body ("Apps.Core.Models.Core_Models") use',
        '"apps/core/models/core_models.adb";',
        'for Spec ("Apps.OCPP.Models.OCPP_Models") use',
        '"apps/ocpp/models/ocpp_models.ads";',
        'for Body ("Apps.OCPP.Models.OCPP_Models") use',
        '"apps/ocpp/models/ocpp_models.adb";',
    ]

    for gpr_entry in expected_gpr_entries:
        assert gpr_entry in gpr_text
