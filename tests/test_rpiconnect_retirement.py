"""Repository-level guards for the completed Raspberry Pi Connect retirement."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings.apps import PROJECT_LOCAL_APPS
from utils.role_app_profiles import (
    FEATURE_PACK_APP_SELECTORS,
    ROLE_DEFAULT_APP_SELECTORS,
    RoleProfile,
    resolve_role_app_selectors,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RETIRED_APP = "rpi" + "connect"
RETIRED_SELECTOR = "apps." + RETIRED_APP
RETIRED_PACK_PREFIX = "rpi_" + "connect"


def test_retired_fleet_app_package_is_absent() -> None:
    assert not (REPO_ROOT / "apps" / RETIRED_APP).exists()


def test_retired_fleet_app_is_not_registered_or_role_selected() -> None:
    assert RETIRED_SELECTOR not in PROJECT_LOCAL_APPS
    assert RETIRED_SELECTOR not in ROLE_DEFAULT_APP_SELECTORS[RoleProfile.CONTROL]
    assert RETIRED_SELECTOR not in resolve_role_app_selectors("control")


def test_retired_feature_packs_are_unknown() -> None:
    retired_packs = {
        RETIRED_PACK_PREFIX,
        RETIRED_PACK_PREFIX + "_updates",
    }
    assert retired_packs.isdisjoint(FEATURE_PACK_APP_SELECTORS)

    for feature_pack in retired_packs:
        with pytest.raises(ValueError, match="Unknown feature pack"):
            resolve_role_app_selectors("watchtower", feature_packs=(feature_pack,))


def test_no_surviving_migration_depends_on_retired_fleet_app() -> None:
    hits: list[str] = []
    for migration in REPO_ROOT.glob("apps/*/migrations/*.py"):
        if RETIRED_APP in migration.read_text(encoding="utf-8").lower():
            hits.append(migration.relative_to(REPO_ROOT).as_posix())

    assert hits == []


def test_no_executable_source_reintroduces_retired_fleet_app() -> None:
    marker = RETIRED_APP
    this_file = Path(__file__).resolve()
    hits: list[str] = []

    for path in REPO_ROOT.rglob("*.py"):
        if path.resolve() == this_file:
            continue
        relative = path.relative_to(REPO_ROOT)
        if any(part in {".git", ".venv", "venv", "node_modules"} for part in relative.parts):
            continue
        if marker in path.read_text(encoding="utf-8").lower():
            hits.append(relative.as_posix())

    assert hits == []
