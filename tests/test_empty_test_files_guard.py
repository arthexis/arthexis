"""Regression guard that blocks empty pytest modules from being introduced."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_EMPTY_TEST_FILES = {
    "apps/awg/tests/test_awg_calculate.py",
    "apps/awg/tests/test_cable_fixtures.py",
    "apps/awg/tests/test_ev_charging_calculator.py",
    "apps/awg/tests/test_power_calculator.py",
    "apps/cards/tests/test_admin_dashboard.py",
    "apps/cards/tests/test_rfid_peer_sync.py",
    "apps/core/tests/test_calculate_coverage_command.py",
    "apps/core/tests/test_usage_analytics.py",
    "apps/counters/tests/test_system_dashboard_rules_report.py",
    "apps/counters/tests/test_user_story_dashboard_rules.py",
    "apps/desktop/tests/test_admin.py",
    "apps/desktop/tests/test_services.py",
    "apps/fitbit/tests/test_fitbit_command.py",
    "apps/flows/tests/test_transitions.py",
    "apps/ftp/tests/test_authorizers.py",
    "apps/ftp/tests/test_utils.py",
    "apps/gdrive/tests/test_services.py",
    "apps/locals/tests/test_admin.py",
    "apps/locals/tests/test_dashboard_favorites.py",
    "apps/mermaid/tests/test_models.py",
    "apps/nfts/tests/test_models.py",
    "apps/nodes/tests/test_legacy_node_command_wrappers.py",
    "apps/ocpp/tests/test_coverage_ocpp16_command.py",
    "apps/ocpp/tests/test_status_resets.py",
    "apps/ops/tests/test_dashboard_rules.py",
    "apps/ops/tests/test_models.py",
    "apps/ops/tests/test_tasks.py",
    "apps/protocols/tests/test_ocpp16_coverage.py",
    "apps/protocols/tests/test_ocpp201_coverage.py",
    "apps/protocols/tests/test_ocpp21_coverage.py",
    "apps/reports/tests/test_system_sql_report.py",
    "apps/screens/tests/test_models.py",
    "apps/screens/tests/test_startup_notifications_lcd.py",
    "apps/sites/tests/test_dashboard_badges.py",
    "apps/sites/tests/test_view_history.py",
    "apps/tasks/tests/test_forms.py",
    "apps/tasks/tests/test_models.py",
    "apps/tests/domain/test_results.py",
    "apps/tests/management/commands/test.py",
    "apps/users/management/commands/test_login.py",
    "apps/users/tests/test_create_docs_admin_command.py",
    "apps/users/tests/test_temp_password_command.py",
    "apps/video/tests/test_camera_service_command.py",
    "apps/video/tests/test_snapshot_command.py",
    "apps/video/tests/test_video_debug_command.py",
    "apps/wikis/tests/test_models.py",
    "apps/wikis/tests/test_services.py",
    "scripts/test_server.py",
    "tests/test_admindocs_index.py",
    "tests/test_admin_modules.py",
    "tests/test_arthexis_lazy_import.py",
    "tests/test_reports_syntax.py",
}


def _contains_collectable_tests(module_path: Path) -> bool:
    """Return ``True`` when a file defines at least one collectable test object."""

    module = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test"):
                    return True
    return False


def _iter_test_modules() -> list[Path]:
    """Collect Python test module paths using the repository naming conventions."""

    candidates = [
        *REPO_ROOT.glob("tests/test_*.py"),
        *REPO_ROOT.glob("apps/**/tests/test_*.py"),
        *REPO_ROOT.glob("apps/**/management/commands/test*.py"),
        *REPO_ROOT.glob("scripts/test*.py"),
    ]
    return sorted({path for path in candidates if path.name != "__init__.py"})


@pytest.mark.regression
def test_no_new_empty_test_files_are_introduced() -> None:
    """Prevent adding new test files that do not define any tests yet."""

    empty_modules = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in _iter_test_modules()
        if not _contains_collectable_tests(path)
    }

    assert empty_modules <= ALLOWED_EMPTY_TEST_FILES, (
        "Found new empty test modules. Add at least one test to each new module, "
        "or deliberately update ALLOWED_EMPTY_TEST_FILES if this is intentional: "
        f"{sorted(empty_modules - ALLOWED_EMPTY_TEST_FILES)}"
    )
