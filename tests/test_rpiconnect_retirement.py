"""Repository-level guards for the staged Raspberry Pi Connect retirement."""

from __future__ import annotations

from pathlib import Path

from django.core.management import get_commands

from utils.role_app_profiles import resolve_role_app_selectors

REPO_ROOT = Path(__file__).resolve().parents[1]

# These references are intentionally retained until the follow-up physical-delete PR:
# Django must still discover the app to apply 0003 on upgraded installations, and
# role/feature-pack configuration may still contain the selector during that bridge
# release. The selected app is a migration-only shell with no runtime surface.
COMPATIBILITY_REFERENCE_FILES = {
    Path("apps/rpiconnect/apps.py"),
    Path("apps/rpiconnect/manifest.py"),
    Path("apps/rpiconnect/routes.py"),
    Path("apps/rpiconnect/migrations/0001_initial.py"),
    Path("apps/rpiconnect/migrations/0002_initial.py"),
    Path("apps/rpiconnect/migrations/0003_retire_rpiconnect.py"),
    Path("apps/rpiconnect/tests/test_rpiconnect_smoke.py"),
    Path("config/settings/apps.py"),
    Path("tests/test_role_app_profiles.py"),
    Path("tests/test_rpiconnect_retirement.py"),
    Path("utils/role_app_profiles.py"),
}
EXECUTABLE_SUFFIXES = {".py", ".html", ".sh", ".toml", ".yaml", ".yml"}
MARKERS = ("apps.rpiconnect", "RPICONNECT_", "rpi_connect")


def test_retired_rpiconnect_has_no_management_command() -> None:
    assert "reconcile_rpiconnect" not in get_commands()


def test_role_selection_can_only_reach_the_migration_shell() -> None:
    control_apps = set(resolve_role_app_selectors("control"))
    assert "apps.rpiconnect" in control_apps

    # Compatibility selection is allowed for this bridge release, but every
    # operational module that used to make the selector meaningful is gone.
    for relative_path in (
        "apps/rpiconnect/admin.py",
        "apps/rpiconnect/models.py",
        "apps/rpiconnect/urls.py",
        "apps/rpiconnect/views.py",
        "apps/rpiconnect/services/campaign_service.py",
        "apps/rpiconnect/services/ingestion_service.py",
        "apps/rpiconnect/management/commands/reconcile_rpiconnect.py",
    ):
        assert not (REPO_ROOT / relative_path).exists()


def test_no_unexpected_runtime_rpiconnect_references_remain() -> None:
    unexpected: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in EXECUTABLE_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT)
        if relative in COMPATIBILITY_REFERENCE_FILES:
            continue
        if any(part in {".git", ".venv", "venv", "node_modules"} for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in text for marker in MARKERS):
            unexpected.append(relative.as_posix())

    assert unexpected == []


def test_ingestion_secret_contract_is_gone() -> None:
    hits: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT)
        if relative == Path("tests/test_rpiconnect_retirement.py"):
            continue
        if "RPICONNECT_INGESTION_TOKEN" in path.read_text(encoding="utf-8"):
            hits.append(relative.as_posix())

    assert hits == []
