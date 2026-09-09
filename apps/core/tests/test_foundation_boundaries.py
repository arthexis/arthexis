"""Architecture checks for foundational primitives extracted from apps.core."""

from __future__ import annotations

import ast
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parents[2]
BASE_MODELS = APPS_DIR / "base" / "models.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_base_user_manager_does_not_import_groups_domain() -> None:
    imports = _imported_modules(BASE_MODELS)

    assert not any(
        module == "apps.groups" or module.startswith("apps.groups.")
        for module in imports
    ), "apps.base must publish lifecycle events instead of importing groups policy"


def test_core_ownable_path_is_only_a_compatibility_alias() -> None:
    from apps.base.ownership import Ownable as BaseOwnable
    from apps.core.models.ownable import Ownable as CoreOwnable

    assert CoreOwnable is BaseOwnable


def test_core_fixture_helper_path_is_only_a_compatibility_alias() -> None:
    from apps.base.fixtures import ensure_seed_data_flags as base_helper
    from apps.core.fixtures import ensure_seed_data_flags as core_helper

    assert core_helper is base_helper
