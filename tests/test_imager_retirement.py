"""Repository-level guards for permanent retirement of Raspberry Pi imaging."""

from __future__ import annotations

from pathlib import Path

from config.settings.apps import PROJECT_LOCAL_APPS, _resolve_installed_app_entries
from utils.role_app_profiles import RoleProfile, resolve_role_app_selectors

REPO_ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_SUFFIXES = {".py", ".html", ".sh", ".toml", ".yaml", ".yml"}
MARKERS = (
    "apps.imager",
    "IMAGER_",
    "RaspberryPiImageArtifact",
    "RaspberryPiImageBurnJob",
)
# Keep explicit negative assertions and stale-lock filtering without allowing the
# retired app to reappear anywhere else in executable repository source.
ALLOWED_REFERENCE_FILES = {
    Path("tests/test_imager_retirement.py"),
    Path("tests/test_role_app_profiles.py"),
    Path("utils/role_app_profiles.py"),
}


def test_imager_package_is_absent() -> None:
    assert not (REPO_ROOT / "apps/imager").exists()


def test_imager_is_not_registered_as_local_app() -> None:
    assert "apps.imager" not in PROJECT_LOCAL_APPS


def test_no_other_app_migration_depends_on_imager() -> None:
    hits: list[str] = []
    for path in (REPO_ROOT / "apps").glob("*/migrations/*.py"):
        text = path.read_text(encoding="utf-8")
        if "imager" in text:
            hits.append(path.relative_to(REPO_ROOT).as_posix())
    assert hits == []


def test_no_role_selects_retired_imager() -> None:
    for role in RoleProfile:
        selected = set(resolve_role_app_selectors(role))
        assert "apps.imager" not in selected


def test_enabled_app_lock_cannot_reintroduce_removed_imager() -> None:
    selected = set(
        _resolve_installed_app_entries(
            node_role="Terminal",
            profile_enabled=False,
            enabled_app_lock_entries=("apps.imager.apps.ImagerConfig",),
        )
    )

    assert "apps.imager" not in selected
    assert "apps.imager.apps.ImagerConfig" not in selected


def test_no_unexpected_imager_runtime_references_remain() -> None:
    unexpected: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in EXECUTABLE_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT)
        if relative in ALLOWED_REFERENCE_FILES:
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
