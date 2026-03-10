"""Regression tests for static import resolution checks."""

from __future__ import annotations

import ast
from pathlib import Path

from scripts import check_import_resolution


def test_relative_from_package_init_exports_is_treated_as_resolvable(
    tmp_path: Path,
) -> None:
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


def test_relative_from_package_init_without_export_stays_unresolved(
    tmp_path: Path,
) -> None:
    """Names not defined/re-exported by package __init__ should still fail."""

    package_dir = tmp_path / "apps" / "core" / "tasks" / "auto_upgrade"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        "__all__ = ['different_name']\n", encoding="utf-8"
    )
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


def test_relative_from_package_mixed_names_only_reports_unresolved(
    tmp_path: Path,
) -> None:
    """Multi-name imports should still report names missing from __init__ and filesystem."""

    package_dir = tmp_path / "apps" / "core" / "tasks" / "auto_upgrade"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        "__all__ = ['exported_name']\n",
        encoding="utf-8",
    )
    (package_dir / "sibling_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    module_path = package_dir / "tasks.py"
    module_path.write_text(
        "from . import exported_name, sibling_module, missing_name\n",
        encoding="utf-8",
    )

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    collector = check_import_resolution.ImportCollector(
        file_path=module_path,
        package="apps.core.tasks.auto_upgrade",
    )

    collector.visit(tree)

    assert len(collector.issues) == 1
    assert collector.issues[0].module == "missing_name"


def test_collect_target_files_returns_repo_python_files_when_no_paths(
    monkeypatch, tmp_path: Path
) -> None:
    """Empty CLI paths should fall back to scanning the configured project root."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    target = project_root / "module.py"
    target.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(check_import_resolution, "PROJECT_ROOT", project_root)

    files = check_import_resolution._collect_target_files([])

    assert files == [target]


def test_collect_target_files_filters_to_python_files_under_project_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """CLI file filtering should ignore non-Python and out-of-repo targets."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    in_repo = project_root / "apps" / "sample.py"
    in_repo.parent.mkdir(parents=True)
    in_repo.write_text("VALUE = 1\n", encoding="utf-8")

    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 2\n", encoding="utf-8")

    monkeypatch.setattr(check_import_resolution, "PROJECT_ROOT", project_root)

    files = check_import_resolution._collect_target_files([str(in_repo), str(outside)])

    assert files == [in_repo]


def test_collect_target_files_ignores_explicit_files_in_ignored_dirs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Explicit files inside ignored directories should be excluded."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    migration = project_root / "apps" / "sample" / "__pycache__" / "cached.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.setattr(check_import_resolution, "PROJECT_ROOT", project_root)

    files = check_import_resolution._collect_target_files([str(migration)])

    assert files == []
