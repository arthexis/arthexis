import py_compile
from pathlib import Path

import pytest

from django.conf import settings

@pytest.mark.integration
def test_reports_module_syntax() -> None:
    reports_dir = Path(settings.BASE_DIR) / "apps/core/views/reports"
    for reports_path in reports_dir.glob("*.py"):
        py_compile.compile(str(reports_path), doraise=True)
