"""Minimal pytest bootstrap for project-wide plugin loading."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

ROOT_DIR = Path(__file__).resolve().parent

# Ensure the repository root is importable in environments where pytest is
# launched from a different working directory.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.plugins.sqlite_paths import sqlite_env_summary_lines
from tests.pytest_bootstrap import apply_bootstrap

pytest_plugins = [
    "tests.plugins.markers",
    "tests.plugins.db_bootstrap",
]

apply_bootstrap(ROOT_DIR)

AUDIO_APP_SELECTOR = "apps.audio"
AWG_APP_SELECTOR = "apps.awg"
AWS_APP_SELECTOR = "apps.aws"
CALENDARS_APP_SELECTOR = "apps.calendars"
CHATS_APP_SELECTOR = "apps.chats"
CLASSIFICATION_APP_SELECTOR = "apps.classification"
DEPLOY_APP_SELECTOR = "apps.deploy"
EMBEDS_APP_SELECTOR = "apps.embeds"
EVERGO_APP_SELECTOR = "apps.evergo"
FTP_APP_SELECTOR = "apps.ftp"
GDRIVE_APP_SELECTOR = "apps.gdrive"
LEADS_APP_SELECTOR = "apps.leads"
LIBRARY_APP_SELECTOR = "apps.library"
META_APP_SELECTOR = "apps.meta"
ODOO_APP_SELECTOR = "apps.odoo"
PAYMENTS_APP_SELECTOR = "apps.payments"
PLAYWRIGHT_APP_SELECTOR = "apps.playwright"
PROJECTS_APP_SELECTOR = "apps.projects"
RATES_APP_SELECTOR = "apps.rates"
SCREENS_APP_SELECTOR = "apps.screens"
SHOP_APP_SELECTOR = "apps.shop"
SIMULATORS_APP_SELECTOR = "apps.simulators"
SURVEY_APP_SELECTOR = "apps.survey"
TEAMS_APP_SELECTOR = "apps.teams"
TERMS_APP_SELECTOR = "apps.terms"
VEHICLE_APP_SELECTOR = "apps.vehicle"
VIDEO_APP_SELECTOR = "apps.video"

OPTIONAL_APP_TEST_REQUIREMENTS = {
    "apps/audio/tests": (AUDIO_APP_SELECTOR,),
    "apps/awg/tests": (AWG_APP_SELECTOR,),
    "apps/aws/tests": (AWS_APP_SELECTOR,),
    "apps/calendars/tests": (CALENDARS_APP_SELECTOR,),
    "apps/chats/tests": (CHATS_APP_SELECTOR,),
    "apps/classification/tests": (CLASSIFICATION_APP_SELECTOR,),
    "apps/deploy/tests": (DEPLOY_APP_SELECTOR,),
    "apps/embeds/tests": (EMBEDS_APP_SELECTOR,),
    "apps/evergo/tests": (EVERGO_APP_SELECTOR,),
    "apps/ftp/tests": (FTP_APP_SELECTOR,),
    "apps/gdrive/tests": (GDRIVE_APP_SELECTOR,),
    "apps/leads/tests": (LEADS_APP_SELECTOR,),
    "apps/library/tests": (LIBRARY_APP_SELECTOR,),
    "apps/meta/tests": (META_APP_SELECTOR,),
    "apps/odoo/tests": (ODOO_APP_SELECTOR,),
    "apps/payments/tests": (PAYMENTS_APP_SELECTOR,),
    "apps/playwright/tests": (PLAYWRIGHT_APP_SELECTOR,),
    "apps/projects/tests": (PROJECTS_APP_SELECTOR,),
    "apps/rates/tests": (RATES_APP_SELECTOR,),
    "apps/screens/tests": (SCREENS_APP_SELECTOR,),
    "apps/simulators/tests": (SIMULATORS_APP_SELECTOR,),
    "apps/survey/tests": (SURVEY_APP_SELECTOR,),
    "apps/teams/tests": (TEAMS_APP_SELECTOR,),
    "apps/terms/tests": (TERMS_APP_SELECTOR,),
    "apps/vehicle/tests": (VEHICLE_APP_SELECTOR,),
    "apps/video/tests": (VIDEO_APP_SELECTOR,),
    "apps/core/tests/test_email_inbox_admin.py": (ODOO_APP_SELECTOR,),
    "apps/core/tests/test_odoo_product_admin.py": (ODOO_APP_SELECTOR,),
    "apps/core/tests/test_odoo_quote_report.py": (ODOO_APP_SELECTOR,),
    "apps/core/tests/test_ownable.py": (CHATS_APP_SELECTOR,),
    "apps/emails/tests/test_collector_notifications.py": (ODOO_APP_SELECTOR,),
    "apps/media/tests/test_media_serving.py": (CHATS_APP_SELECTOR,),
    "apps/nodes/tests/test_device_sync.py": (AUDIO_APP_SELECTOR,),
    "apps/ocpp/tests/test_charge_point_simulator_session.py": (
        SIMULATORS_APP_SELECTOR,
    ),
    "apps/ocpp/tests/test_runtime.py": (SIMULATORS_APP_SELECTOR,),
    "apps/ocpp/tests/test_supported_charger_templates.py": (SIMULATORS_APP_SELECTOR,),
    "apps/ocpp/tests/test_websocket_creation.py": (
        RATES_APP_SELECTOR,
        SIMULATORS_APP_SELECTOR,
    ),
    "apps/ocpp/tests/test_websocket_routing.py": (VIDEO_APP_SELECTOR,),
    "apps/ops/tests/test_operator_journey.py": (SHOP_APP_SELECTOR, VIDEO_APP_SELECTOR),
    "apps/release/tests/test_data_transforms.py": (VIDEO_APP_SELECTOR,),
    "apps/sensors/tests/test_usb_lcd.py": (AUDIO_APP_SELECTOR, VIDEO_APP_SELECTOR),
    "apps/sites/tests/test_public_routes.py": (META_APP_SELECTOR,),
}


def _collection_relative_path(collection_path: Path | str) -> str | None:
    try:
        return Path(str(collection_path)).resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return None


def _missing_optional_test_requirements(rel_path: str) -> tuple[str, ...]:
    installed_apps = set(settings.INSTALLED_APPS)
    for test_path, required_apps in OPTIONAL_APP_TEST_REQUIREMENTS.items():
        if rel_path == test_path or rel_path.startswith(f"{test_path.rstrip('/')}/"):
            return tuple(app for app in required_apps if app not in installed_apps)
    return ()


def pytest_ignore_collect(collection_path: Path, config: Any) -> bool | None:
    """Skip tests that import disabled optional runtime apps during collection."""

    rel_path = _collection_relative_path(collection_path)
    if rel_path is None:
        return None
    if _missing_optional_test_requirements(rel_path):
        return True
    return None


def pytest_report_header(config: Any) -> list[str]:
    """Include selected SQLite paths near the top of pytest output."""

    return [f"Arthexis SQLite {line}" for line in sqlite_env_summary_lines()]


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: Any
) -> None:
    """Repeat SQLite paths in failure summaries so setup errors are diagnosable."""

    if exitstatus == 0:
        return
    terminalreporter.section("Arthexis SQLite paths", sep="-")
    for line in sqlite_env_summary_lines():
        terminalreporter.write_line(line)


@pytest.fixture(autouse=True)
def restore_mutable_path_settings() -> Iterator[None]:
    """Reset mutable path settings after each test to avoid cross-test leakage."""

    original_base_dir = settings.BASE_DIR
    original_log_dir = settings.LOG_DIR
    original_static_root = settings.STATIC_ROOT
    try:
        yield
    finally:
        settings.BASE_DIR = original_base_dir
        settings.LOG_DIR = original_log_dir
        settings.STATIC_ROOT = original_static_root
