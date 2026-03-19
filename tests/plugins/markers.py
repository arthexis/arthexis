"""Collection-time marker behavior for platform-specific test selection."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Apply global collection-time marker behavior used across the test suite."""

    del config, session
    is_windows = os.name == "nt"
    windows_nmcli_skip = pytest.mark.skip(reason="nmcli setup script tests are not supported on Windows environments")

    for item in items:
        if is_windows and item.nodeid.startswith("scripts/tests/test_nmcli_setup_script.py"):
            item.add_marker(windows_nmcli_skip)
