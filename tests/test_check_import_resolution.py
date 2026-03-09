"""Tests for static import resolution policy around optional imports."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_import_resolution.py"
_SPEC = importlib.util.spec_from_file_location("check_import_resolution", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_CHECK_IMPORT_RESOLUTION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CHECK_IMPORT_RESOLUTION)

ImportCollector = _CHECK_IMPORT_RESOLUTION.ImportCollector


def _collect_issues(source: str) -> list[object]:
    """Return collected import issues for the provided source string."""

    tree = ast.parse(source)
    collector = ImportCollector(Path("tmp/example.py"), package=None)
    collector.visit(tree)
    return collector.issues


def test_importerror_try_without_marker_reports_unresolved_import() -> None:
    """Unmarked ``try/except ImportError`` blocks must still validate imports."""

    issues = _collect_issues(
        """
try:
    import definitely_missing_module_xyz
except ImportError:
    pass
"""
    )

    assert len(issues) == 1
    assert issues[0].module == "definitely_missing_module_xyz"


def test_marked_optional_import_allows_unresolved_import() -> None:
    """Explicit optional marker enables skipping unresolved imports in ``try`` blocks."""

    issues = _collect_issues(
        """
try:
    "optional-import"
    import definitely_missing_module_xyz
except ImportError:
    pass
"""
    )

    assert issues == []


def test_optional_modules_remain_allowed_without_marker() -> None:
    """Known platform-optional modules continue to be accepted."""

    issues = _collect_issues(
        """
try:
    import smbus
except ImportError:
    pass
"""
    )

    assert issues == []
