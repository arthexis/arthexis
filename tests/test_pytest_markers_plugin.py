"""Unit tests for collection-time marker plugin behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from tests.plugins import markers


@dataclass
class FakeMarker:
    """Minimal marker object used to model pytest marker arguments."""

    name: str
    args: tuple[Any, ...] = ()


@dataclass
class FakeItem:
    """Minimal pytest item stub for unit-testing collection hook logic."""

    nodeid: str
    existing_markers: list[FakeMarker] = field(default_factory=list)
    fixturenames: list[str] = field(default_factory=list)
    cls: type[Any] | None = None
    added_markers: list[Any] = field(default_factory=list)

    def get_closest_marker(self, name: str) -> FakeMarker | None:
        """Return the most recently attached marker with the provided name."""

        for marker in reversed(self.existing_markers):
            if marker.name == name:
                return marker
        return None

    def iter_markers(self, name: str) -> list[FakeMarker]:
        """Return all markers attached to the fake item matching ``name``."""

        return [marker for marker in self.existing_markers if marker.name == name]

    def add_marker(self, marker: Any) -> None:
        """Record a marker that would be dynamically added during collection."""

        self.added_markers.append(marker)


class FakeConfig:
    """Minimal pytest config stub for testing option rewrites and marker registration."""

    def __init__(self, *, markexpr: str = "", current_pr: str | None = None) -> None:
        self.option = SimpleNamespace(markexpr=markexpr)
        self._current_pr = current_pr
        self.ini_lines: list[tuple[str, str]] = []

    def getoption(self, name: str) -> str | None:
        """Return fake CLI options used by the marker plugin."""

        if name != "--current-pr":  # pragma: no cover - defensive programming
            raise ValueError(name)
        return self._current_pr

    def addinivalue_line(self, key: str, value: str) -> None:
        """Capture marker registration lines configured by the plugin."""

        self.ini_lines.append((key, value))


def test_pytest_configure_registers_pr_current_marker() -> None:
    """Marker plugin should register the dynamic ``pr_current`` marker."""

    config = FakeConfig(markexpr="critical")

    markers.pytest_configure(config)  # type: ignore[arg-type]

    assert config.option.markexpr == "critical"
    assert ("markers", "pr_current: dynamically applied to tests whose pytest.mark.pr reference matches --current-pr") in config.ini_lines


def test_collection_does_not_promote_regression_to_critical() -> None:
    """Regression-marked tests should no longer receive implicit ``critical`` marks."""

    item = FakeItem(nodeid="tests/test_example.py::test_case", existing_markers=[FakeMarker("regression")])
    config = FakeConfig()

    markers.pytest_collection_modifyitems(SimpleNamespace(), config, [item])

    assert "critical" not in item.added_markers


def test_collection_marks_current_pr_and_extends_markexpr() -> None:
    """Matching PR markers should map to ``pr_current`` and update ``markexpr``."""

    item = FakeItem(
        nodeid="tests/test_example.py::test_case",
        existing_markers=[FakeMarker("pr", args=("pr-42",))],
    )
    config = FakeConfig(markexpr="critical", current_pr="PR-42")

    markers.pytest_collection_modifyitems(SimpleNamespace(), config, [item])

    assert "pr_current" in item.added_markers
    assert config.option.markexpr == "(critical) or pr_current"
