"""Minimal pytest bootstrap for project-wide plugin loading."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# Ensure the repository root is importable in environments where pytest is
# launched from a different working directory.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.pytest_bootstrap import apply_bootstrap

pytest_plugins = [
    "tests.plugins.markers",
    "tests.plugins.db_bootstrap",
    "tests.plugins.result_capture",
]

apply_bootstrap(ROOT_DIR)
