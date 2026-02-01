from __future__ import annotations

from pathlib import Path
import py_compile


def test_reports_module_syntax() -> None:
    reports_path = Path(__file__).resolve().parents[1] / "apps/core/views/reports.py"
    py_compile.compile(str(reports_path), doraise=True)
