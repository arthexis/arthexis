"""Collection-time marker behavior for regression and PR-scoped selection."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register suite-level options for PR-scoped dynamic test selection."""

    parser.addoption(
        "--current-pr",
        action="store",
        default=None,
        metavar="PR",
        help="PR reference used to include tests marked for the current change set.",
    )


def normalize_pr_reference(value: object) -> str | None:
    """Normalize marker and CLI PR references into comparable uppercase strings."""

    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized or None


def pytest_configure(config: pytest.Config) -> None:
    """Keep legacy mark-expression behavior and register dynamic PR markers."""

    markexpr = getattr(config.option, "markexpr", "")
    if markexpr and "critical" in markexpr and "regression" not in markexpr:
        config.option.markexpr = f"({markexpr}) or regression"
    config.addinivalue_line(
        "markers",
        "pr_current: dynamically applied to tests whose pytest.mark.pr reference matches --current-pr",
    )


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Apply global collection-time marker behavior used across the test suite."""

    del session
    is_windows = os.name == "nt"
    windows_nmcli_skip = pytest.mark.skip(reason="nmcli setup script tests are not supported on Windows environments")
    selected_pr = normalize_pr_reference(config.getoption("--current-pr"))
    should_extend_markexpr = bool(selected_pr)

    for item in items:
        if item.get_closest_marker("regression") and not item.get_closest_marker("critical"):
            item.add_marker("critical")

        if selected_pr:
            for marker in item.iter_markers(name="pr"):
                marker_reference = normalize_pr_reference(marker.args[0] if marker.args else None)
                if marker_reference == selected_pr:
                    item.add_marker("pr_current")
                    break

        if is_windows and item.nodeid.startswith("scripts/tests/test_nmcli_setup_script.py"):
            item.add_marker(windows_nmcli_skip)

    if should_extend_markexpr:
        markexpr = (config.option.markexpr or "").strip()
        if not markexpr:
            config.option.markexpr = "pr_current"
        elif "pr_current" not in markexpr:
            config.option.markexpr = f"({markexpr}) or pr_current"
