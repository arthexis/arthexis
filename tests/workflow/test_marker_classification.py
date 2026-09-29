from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

ROOT = Path("tests")


def _module_has_workflow_marker(path: Path) -> bool:
    return "pytestmark = pytest.mark.workflow" in path.read_text(encoding="utf-8")


def _function_markers(path: Path) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    markers: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated: set[str] = set()
        for decorator in node.decorator_list:
            try:
                text = ast.unparse(decorator)
            except AttributeError:  # pragma: no cover - Python 3.10 has ast.unparse
                text = ""
            if text.startswith("pytest.mark."):
                decorated.add(text.removeprefix("pytest.mark."))
        markers[node.name] = decorated
    return markers


def test_workflow_directory_modules_are_marked_workflow() -> None:
    files = sorted((ROOT / "workflow").glob("test_*.py"))
    assert files
    assert [str(path) for path in files if not _module_has_workflow_marker(path)] == []


def test_workflow_named_contract_modules_are_marked_workflow() -> None:
    files = sorted((ROOT / "deploy").glob("test_*workflow*.py"))
    files += sorted((ROOT / "ocpp").glob("test_*workflow*.py"))
    assert files
    assert [str(path) for path in files if not _module_has_workflow_marker(path)] == []


def _workflow_reading_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        source = ast.unparse(node)
        if (
            "WORKFLOW" in source
            or "watchtower_deploy_workflow" in source
            or "watchtower_recovery_workflow" in source
            or ".github/workflows/" in source
        ):
            selected.add(node.name)
    return selected


def test_mixed_deploy_modules_mark_workflow_reading_assertions() -> None:
    for path in sorted((ROOT / "deploy").glob("test_*.py")):
        if _module_has_workflow_marker(path):
            continue
        markers = _function_markers(path)
        workflow_tests = _workflow_reading_tests(path)
        assert [
            f"{path}:{name}"
            for name in sorted(workflow_tests)
            if "workflow" not in markers[name]
        ] == []

    remote = _function_markers(ROOT / "deploy" / "test_remote_acceptance.py")
    assert "workflow" not in remote[
        "test_remote_acceptance_validates_oauth_metadata_contract"
    ]


def test_real_migration_executor_tests_are_explicitly_marked() -> None:
    offenders = []
    for path in ROOT.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        if "MigrationExecutor" in text and "pytest.mark.migration" not in text:
            offenders.append(str(path))
    assert offenders == []


def test_reconciliation_e2e_marker_is_registered_and_used() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "reconciliation_e2e: slow end-to-end legacy reconciliation acceptance" in pyproject
    marked = [
        path
        for path in (ROOT / "arthexis" / "reconciliation").glob("test_*.py")
        if "pytest.mark.reconciliation_e2e" in path.read_text(encoding="utf-8")
    ]
    assert marked
