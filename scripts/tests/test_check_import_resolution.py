"""Regression tests for static import resolution checks."""

from __future__ import annotations

import ast
from pathlib import Path

from scripts import check_import_resolution


def test_relative_from_package_init_exports_is_treated_as_resolvable(tmp_path: Path) -> None:
    """`from . import name` should pass when package __init__ exists."""

    package_dir = tmp_path / "apps" / "core" / "tasks" / "auto_upgrade"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        "def get_revision() -> str:\n    return 'abc123'\n",
        encoding="utf-8",
    )
    module_path = package_dir / "tasks.py"
    module_path.write_text("from . import get_revision\n", encoding="utf-8")

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    collector = check_import_resolution.ImportCollector(
        file_path=module_path,
        package="apps.core.tasks.auto_upgrade",
    )

    collector.visit(tree)

    assert collector.issues == []


def test_relative_from_package_missing_name_stays_unresolved(tmp_path: Path) -> None:
    """Missing package __init__ should still be reported."""

    package_dir = tmp_path / "apps" / "core" / "tasks" / "auto_upgrade"
    package_dir.mkdir(parents=True)
    module_path = package_dir / "tasks.py"
    module_path.write_text("from . import get_revision\n", encoding="utf-8")

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    collector = check_import_resolution.ImportCollector(
        file_path=module_path,
        package="apps.core.tasks.auto_upgrade",
    )

    collector.visit(tree)

    assert len(collector.issues) == 1
    assert collector.issues[0].module == "get_revision"
