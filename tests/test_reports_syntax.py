from __future__ import annotations

import py_compile
from pathlib import Path

import pytest
from django.conf import settings


@pytest.mark.critical
def test_reports_module_syntax() -> None:
    reports_path = Path(settings.BASE_DIR) / "apps/core/views/reports.py"
    py_compile.compile(str(reports_path), doraise=True)
