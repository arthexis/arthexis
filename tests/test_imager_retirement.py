"""Repository-level guards for staged retirement of Raspberry Pi imaging."""

from __future__ import annotations

from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError

from utils.role_app_profiles import RoleProfile, resolve_role_app_selectors

REPO_ROOT = Path(__file__).resolve().parents[1]

# The Django app remains discoverable for one bridge release so deployed
# databases can apply 0002_retire_imager. It is no longer runtime-selectable.
COMPATIBILITY_REFERENCE_FILES = {
    Path("apps/imager/apps.py"),
    Path("apps/imager/manifest.py"),
    Path("apps/imager/routes.py"),
    Path("apps/imager/management/commands/imager.py"),
    Path("apps/imager/migrations/0001_initial.py"),
    Path("apps/imager/migrations/0002_retire_imager.py"),
    Path("config/settings/apps.py"),
    Path("tests/test_imager_retirement.py"),
    Path("tests/test_role_app_profiles.py"),
    Path("utils/role_app_profiles.py"),
}
EXECUTABLE_SUFFIXES = {".py", ".html", ".sh", ".toml", ".yaml", ".yml"}
MARKERS = (
    "apps.imager",
    "IMAGER_",
    "RaspberryPiImageArtifact",
    "RaspberryPiImageBurnJob",
)


def test_imager_registers_no_runtime_models() -> None:
    assert list(apps.get_app_config("imager").get_models()) == []


def test_imager_exposes_no_root_routes() -> None:
    from apps.imager import routes

    assert routes.ROOT_URLPATTERNS == []


def test_imager_command_refuses_retired_operations() -> None:
    try:
        call_command("imager")
    except CommandError as exc:
        assert "official Raspberry Pi provisioning tools" in str(exc)
    else:
        raise AssertionError("retired imager command unexpectedly executed")


def test_imager_has_terminal_retirement_migration() -> None:
    migration = REPO_ROOT / "apps/imager/migrations/0002_retire_imager.py"
    text = migration.read_text(encoding="utf-8")
    assert '("imager", "0001_initial")' in text
    assert 'DeleteModel(name="RaspberryPiImageBurnJob")' in text
    assert 'DeleteModel(name="RaspberryPiImageArtifact")' in text


def test_imager_operational_modules_are_gone() -> None:
    for relative_path in (
        "apps/imager/admin.py",
        "apps/imager/burner.py",
        "apps/imager/initial_profile.py",
        "apps/imager/models.py",
        "apps/imager/node_features.py",
        "apps/imager/reservations.py",
        "apps/imager/services",
        "apps/imager/templates",
        "apps/imager/tests",
        "apps/imager/urls.py",
        "apps/imager/usb_stability.py",
    ):
        assert not (REPO_ROOT / relative_path).exists()


def test_no_other_app_migration_depends_on_imager() -> None:
    hits: list[str] = []
    for path in (REPO_ROOT / "apps").glob("*/migrations/*.py"):
        relative = path.relative_to(REPO_ROOT)
        if relative.parts[:2] == ("apps", "imager"):
            continue
        text = path.read_text(encoding="utf-8")
        if "imager" in text:
            hits.append(relative.as_posix())
    assert hits == []


def test_no_unexpected_imager_runtime_references_remain() -> None:
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


def test_no_role_selects_retired_imager() -> None:
    for role in RoleProfile:
        selected = set(resolve_role_app_selectors(role))
        assert "apps.imager" not in selected
