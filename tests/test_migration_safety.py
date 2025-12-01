import ast
from pathlib import Path

import pytest


MIGRATION_FILES = [
    path
    for path in Path("apps").glob("*/migrations/*.py")
    if path.name != "__init__.py"
]


@pytest.mark.parametrize("migration_path", MIGRATION_FILES)
def test_migrations_do_not_import_app_models_directly(migration_path: Path) -> None:
    source = migration_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("apps.") and ".models" in module:
                pytest.fail(
                    f"{migration_path} imports '{module}', use migration-safe helpers instead."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name.startswith("apps.") and ".models" in name:
                    pytest.fail(
                        f"{migration_path} imports '{name}', use migration-safe helpers instead."
                    )
