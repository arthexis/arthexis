"""Minimal pytest bootstrap for project-wide plugin loading."""

from __future__ import annotations

from pathlib import Path

from tests.pytest_bootstrap import apply_bootstrap

pytest_plugins = [
    "tests.plugins.markers",
    "tests.plugins.db_bootstrap",
    "tests.plugins.result_capture",
]

apply_bootstrap(Path(__file__).resolve().parent)
