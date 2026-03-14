"""Critical checks for PR-origin marker coverage on restored regression tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.critical, pytest.mark.pr_origin(6216)]


def _has_pr_origin_marker(decorators: list[ast.expr]) -> bool:
    """Return whether a decorator list contains ``pytest.mark.pr_origin(...)``."""

    for decorator in decorators:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute) or func.attr != "pr_origin":
            continue
        mark_attr = func.value
        if not isinstance(mark_attr, ast.Attribute) or mark_attr.attr != "mark":
            continue
        if isinstance(mark_attr.value, ast.Name) and mark_attr.value.id == "pytest":
            return True
    return False


def _test_functions_without_pr_origin(path: Path) -> list[str]:
    """Collect test function names in ``path`` that lack a per-test PR-origin decorator."""

    module = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[str] = []

    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            if not _has_pr_origin_marker(node.decorator_list):
                missing.append(node.name)

    return missing


@pytest.mark.parametrize(
    "file_path",
    [
        Path("apps/actions/tests/test_admin.py"),
        Path("apps/evergo/tests/test_public_views.py"),
        Path("apps/projects/tests/test_admin.py"),
    ],
)
def test_restored_regressions_include_pr_origin_marker(file_path: Path) -> None:
    """Ensure restored regression tests keep explicit PR-origin traceability markers."""

    missing = _test_functions_without_pr_origin(file_path)
    assert missing == [], f"Missing @pytest.mark.pr_origin on tests in {file_path}: {missing}"
