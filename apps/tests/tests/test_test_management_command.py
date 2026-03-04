"""Tests for the consolidated ``test`` management command."""

from __future__ import annotations

import sys

import pytest
from django.core.management import CommandError, call_command

from apps.tests.discovery import _infer_app_label, _normalize_marks
from apps.tests.models import SuiteTest


def test_test_command_rejects_unknown_action() -> None:
    """Regression: unsupported actions should raise a command error."""

    from apps.tests.management.commands.test import Command

    with pytest.raises(CommandError, match="Unsupported action"):
        Command().handle(action="invalid", pytest_args=[])


def test_test_server_subcommand_does_not_require_vscode_cli(monkeypatch) -> None:
    """Regression: ``test server`` should execute via Python module imports only."""

    from apps.tests.management.commands.test import Command

    called: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 0

    monkeypatch.setattr("apps.vscode.test_server.main", fake_main)

    Command()._run_test_server(interval=1.5, debounce=0.5, latest=True)

    assert called["argv"] == ["--interval", "1.5", "--debounce", "0.5", "--latest"]


def test_discover_subcommand_refreshes_suite_tests(monkeypatch) -> None:
    """Regression: ``test discover`` should replace persisted suite metadata."""

    class FakeQuerySet:
        def delete(self) -> tuple[int, dict[str, int]]:
            return 1, {"tests.SuiteTest": 1}

    captured: dict[str, object] = {}

    class FakeManager:
        def all(self) -> FakeQuerySet:
            return FakeQuerySet()

        def bulk_create(self, items):
            captured["items"] = items
            return items

    monkeypatch.setattr(SuiteTest, "objects", FakeManager())

    monkeypatch.setattr(
        "apps.tests.management.commands.test.discover_suite_tests",
        lambda: [
            {
                "node_id": "apps/tests/tests/test_test_management_command.py::test_discover_subcommand_refreshes_suite_tests",
                "name": "test_discover_subcommand_refreshes_suite_tests",
                "module_path": "apps.tests.tests.test_test_management_command",
                "app_label": "tests",
                "class_name": "",
                "marks": ["regression"],
                "file_path": "apps/tests/tests/test_test_management_command.py",
                "is_parameterized": False,
            }
        ],
    )

    call_command("test", "discover")

    created = captured["items"]
    assert len(created) == 1
    assert created[0].node_id == (
        "apps/tests/tests/test_test_management_command.py::"
        "test_discover_subcommand_refreshes_suite_tests"
    )
    assert created[0].app_label == "tests"
    assert created[0].marks == ["regression"]


def test_normalize_marks_filters_builtin_keywords() -> None:
    """Regression: marker normalization should keep only useful custom marks."""

    marks = _normalize_marks(["regression", "django_db", "", "slow", "parametrize"])

    assert marks == ["regression", "slow"]


def test_infer_app_label_from_apps_path() -> None:
    """Infer app labels from repository paths under ``apps/``."""

    assert _infer_app_label("apps/tests/tests/test_test_management_command.py") == "tests"
    assert _infer_app_label("tests/test_misc.py") == ""
