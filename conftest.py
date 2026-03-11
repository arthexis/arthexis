"""Minimal pytest bootstrap for project-wide plugin loading."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from django.conf import settings

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


@pytest.fixture(autouse=True)
def restore_mutable_path_settings() -> Iterator[None]:
    """Reset mutable path settings after each test to avoid cross-test leakage."""

    original_base_dir = settings.BASE_DIR
    original_log_dir = settings.LOG_DIR
    original_static_root = settings.STATIC_ROOT
    try:
        yield
    finally:
        settings.BASE_DIR = original_base_dir
        settings.LOG_DIR = original_log_dir
        settings.STATIC_ROOT = original_static_root
